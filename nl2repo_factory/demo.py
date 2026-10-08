"""Execute the synthetic TagKit verifier against real correct/empty/broken code."""
from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import sys
from pathlib import Path

from .ast_scan import scan_repository
from .common import FactoryError, read_json, require_process, run_process, write_json
from .packaging import materialize_instance
from .specification import clean_start_md, validate_specification
from .verifier import score_output


def run_demo(output: Path) -> dict:
    if importlib.util.find_spec("pytest") is None:
        raise FactoryError("The demo executes real pytest tests. Install pytest in the current Python environment first.")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture = Path(__file__).parent / "fixtures" / "tagkit"
    repo = output / "source"
    shutil.copytree(fixture / "repo", repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS"}}
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    original = require_process(run_process([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                                           cwd=repo, timeout=120, env=env), "Reference fixture tests")
    (output / "reference-test-stdout.txt").write_text(original.stdout + original.stderr)
    observed = score_output(original.stdout + original.stderr, 4, {"tests/test_api.py"})
    if observed["reward"] != 1.0:
        raise FactoryError(f"TagKit reference did not pass all four tests: {observed}")
    payload = scan_repository(repo)
    write_json(output / "ast.json", payload)
    start = output / "start.md"
    start.write_text(clean_start_md((fixture / "start.md").read_text()))
    manifest = read_json(fixture / "api_manifest.json")
    coverage = validate_specification(start.read_text(), manifest, payload)
    write_json(output / "coverage.json", coverage)
    if not coverage["passed"]:
        raise FactoryError(f"TagKit fixture failed specification gate: {coverage}")
    # These are actual host observations, expressly not an A1 Docker pass claim.
    a1 = {"status": "host_fixture_observed", "docker": {"image": "nl2repo-tagkit-dependencies"},
          "env_spec": {"install_commands": [], "test_commands": [shlex.quote(sys.executable) + " -m pytest -q -p no:cacheprovider"],
                       "test_files": ["tests/test_api.py"], "package_files": ["pyproject.toml"], "fixture_files": []},
          "oracle_report": {**observed, "collected": 4, "network_none": False}}
    write_json(output / "host_reference_observation.json", a1)
    instance = output / "instance"
    assets = materialize_instance(repo, instance, start, fixture / "api_manifest.json", a1, payload,
                                  output / "images" / "tagkit.tar", "TagKit")
    write_json(output / "assets.json", assets)
    reports = {}
    for mode in ("oracle", "empty", "broken"):
        workspace = output / "cases" / mode / "workspace"
        logs = output / "cases" / mode / "logs"
        if mode == "empty":
            workspace.mkdir(parents=True)
        else:
            shutil.copytree(instance / "solution" / "oracle", workspace)
        shutil.copy2(start, workspace / "start.md")
        if mode == "broken":
            api = workspace / "tagkit" / "api.py"
            api.write_text(api.read_text().replace(
                '    if not isinstance(text, str):\n        raise InvalidTagInput("text must be a string")\n', ""))
        command = [sys.executable, str(instance / "tests" / "verifier.py"), "--workspace", str(workspace),
                   "--tests-dir", str(instance / "tests"), "--logs-dir", str(logs), "--timeout", "60"]
        require_process(run_process(command, timeout=90, env=env), f"Standalone verifier ({mode})")
        reports[mode] = read_json(logs / "report.json")
    expectations = {"oracle": 1.0, "empty": 0.0, "broken": 0.75}
    passed = all(reports[mode]["reward"] == expected for mode, expected in expectations.items())
    summary = {"schema_version": "nl2repo.demo.v1", "passed": passed, "execution": "host-subprocess",
               "synthetic_fixture": True, "model_called": False, "docker_executed": False,
               "image_built": False, "production_instance_accepted": False,
               "instance_dir": str(instance), "rewards": {mode: report["reward"] for mode, report in reports.items()},
               "expected_rewards": expectations,
               "limitations": ["Host tests do not establish container isolation or image correctness.",
                               "The demo task uses a host Python path; production packaging uses the A1 container commands."]}
    write_json(output / "summary.json", summary)
    if not passed:
        raise FactoryError(f"TagKit negative control failed; see {output / 'summary.json'}")
    return summary
