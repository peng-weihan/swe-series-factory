from __future__ import annotations

import json
import shutil
from pathlib import Path

from nl2repo_factory.common import FactoryError, read_json, write_json
from swe_factory import reports
from swe_factory.snapshots import copy_tree


def emit(destination: Path, problem: Path, instruction: str, solution: str, tests: str,
         config: dict, source: dict, image: str | None):
    destination.mkdir(parents=True, exist_ok=False)
    copy_tree(problem, destination / "environment/problem")
    for folder in ("solution", "tests"):
        (destination / folder).mkdir()
    (destination / "instruction.md").write_text(instruction + "\n")
    (destination / "solution/solution.patch").write_text(solution)
    (destination / "tests/test.patch").write_text(tests)
    write_json(destination / "tests/config.json", config)
    shutil.copy2(Path(__file__).with_name("grader.py"), destination / "tests/grader.py")
    shutil.copy2(Path(reports.__file__), destination / "tests/reports.py")
    (destination / "task.toml").write_text('version = "1.0"\n[metadata]\nfactory = "deepswe-factory"\n'
                                         + 'base_commit = ' + json.dumps(source["commit"]) + '\n')
    (destination / "environment/Dockerfile").write_text(
        "FROM " + (image or "python:3.12-slim") + "\nWORKDIR /workspace\nCOPY problem/ /workspace/\n")
    (destination / "tests/Dockerfile").write_text("FROM " + (image or "python:3.12-slim") + "\nWORKDIR /workspace\n")
    scripts = {
        "solution/solve.sh": '#!/bin/sh\nset -eu\ngit apply --binary "$(dirname "$0")/solution.patch"\n',
        "tests/test.sh": '#!/bin/sh\nset -eu\nexec python3 "$(dirname "$0")/grader.py" "$@"\n',
        "start.sh": '#!/bin/sh\nset -eu\ncd /workspace\ngit init -q\ngit add -Af\n'
                    'git -c user.name=Factory -c user.email=factory@example.invalid -c commit.gpgsign=false commit -qm Problem\n'
                    'git rev-parse HEAD > /tmp/factory-baseline\n',
        "pre_artifacts.sh": '#!/bin/sh\nset -eu\ncd /workspace\ngit add -Af\n'
                            'git diff --cached --binary "$(cat /tmp/factory-baseline)" > /tmp/model.patch\n',
    }
    for path, text in scripts.items():
        target = destination / path
        target.write_text(text)
        target.chmod(0o755)


def selfcheck(task: Path, runner, evidence: Path) -> dict:
    results = {}
    for mode in ("nop", "oracle"):
        argv = ["{python}", "tests/grader.py", "--task", ".", "--output", "verification"]
        if mode == "oracle":
            argv += ["--patch", "solution/solution.patch"]
        execution = runner.run(task, argv, evidence / mode)
        report = read_json(execution["workspace"] / "verification/report.json")
        if execution["execution"].timed_out or execution["execution"].returncode or not report["healthy"]:
            raise FactoryError(f"Packaged {mode} verifier failed")
        results[mode] = report["reward"]
    if results != {"nop": 0.0, "oracle": 1.0}:
        raise FactoryError(f"Packaged Nop/Oracle mismatch: {results}")
    write_json(evidence / "summary.json", results)
    return results
