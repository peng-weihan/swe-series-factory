"""Standalone exported verifier. The controller supplies trusted E, T, and config."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

try:
    from .reports import parse_junit, score
except ImportError:
    from reports import parse_junit, score


def execute(argv, cwd, timeout):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="")
    process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, errors="replace", env=env, start_new_session=True)
    try:
        out, err = process.communicate(timeout=timeout)
        return {"returncode": process.returncode, "stdout": out, "stderr": err, "timed_out": False}
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        out, err = process.communicate()
        return {"returncode": process.returncode, "stdout": out, "stderr": err, "timed_out": True}


def manifest(root):
    result = {}
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix()
        if ".git" in path.relative_to(root).parts:
            continue
        if path.is_symlink():
            raise ValueError("Symlinks are forbidden in verifier workspaces")
        if path.is_file():
            result[name] = (hashlib.sha256(path.read_bytes()).hexdigest(), bool(path.stat().st_mode & 0o111))
    return result


def belongs(name, roots):
    return any(name == r or name.startswith(r.rstrip("/") + "/") for r in roots)


def verify(task: Path, output: Path, patch: Path | None = None):
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads((task / "tests/config.json").read_text())
    repo = output / "workspace"
    shutil.copytree(task / "environment/problem", repo)
    timeout = config["timeout"]
    executions, errors = {}, []
    tests = {}
    try:
        for argv in (["git", "init", "--quiet"], ["git", "add", "--all", "--force"],
                     ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                      "-c", "user.name=Verifier", "-c", "user.email=verifier@example.invalid",
                      "commit", "--quiet", "--allow-empty", "-m", "Problem"]):
            result = execute(argv, repo, timeout)
            if result["returncode"] or result["timed_out"]:
                raise ValueError("Could not initialize verifier baseline")
        baseline = manifest(repo)
        if patch is not None:
            result = execute(["git", "apply", "--binary", str(patch.resolve())], repo, timeout)
            executions["apply_solution"] = result
            if result["returncode"] or result["timed_out"]:
                raise ValueError("Solution patch is not applicable")
            after = manifest(repo)
            changes = {p for p in baseline.keys() | after.keys() if baseline.get(p) != after.get(p)}
            if any(belongs(p, config["test_paths"]) or not belongs(p, config["production_paths"]) for p in changes):
                raise ValueError("Solution edits protected or undeclared paths")
        # T is applied from trusted author assets after validating model ownership.
        result = execute(["git", "apply", "--binary", str((task / "tests/test.patch").resolve())], repo, timeout)
        executions["apply_tests"] = result
        if result["returncode"] or result["timed_out"]:
            raise ValueError("Hidden tests could not be injected")
        build = config.get("build_command", [])
        if build:
            result = execute([sys.executable if v == "{python}" else v for v in build], repo, timeout)
            executions["build"] = result
            if result["returncode"] or result["timed_out"]:
                raise ValueError("Build failed")
        for label in ("target", "regression"):
            report_path = repo / f".factory-{label}.xml"
            report_path.unlink(missing_ok=True)
            argv = [sys.executable if v == "{python}" else v for v in config[label + "_command"]]
            if any("{report}" in v for v in argv):
                argv = [v.replace("{report}", str(report_path)) for v in argv]
            else:
                argv += ["--junitxml=" + str(report_path), "--rootdir=.", "-o", "junit_family=xunit1", "-p", "no:cacheprovider"]
            result = execute(argv, repo, timeout)
            executions[label] = result
            parsed = parse_junit(report_path)
            if report_path.exists():
                shutil.copy2(report_path, output / (label + ".xml"))
            failure = any(s in {"failed", "error"} for s in parsed["tests"].values())
            if (not parsed["healthy"] or result["timed_out"] or result["returncode"] not in (0, 1)
                    or (result["returncode"] == 0 and failure) or (result["returncode"] == 1 and not failure)):
                errors.append(label + ": unhealthy execution")
            for node, status in parsed["tests"].items():
                if tests.get(node, "passed") == "passed":
                    tests[node] = status
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
    reward = score(tests, config["fail_to_pass"], config["pass_to_pass"]) if not errors else 0.0
    result = {"healthy": not errors, "errors": errors, "tests": tests, "reward": reward, "executions": executions}
    (output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    (output / "reward.txt").write_text(str(reward) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--patch", type=Path)
    args = parser.parse_args()
    result = verify(args.task.resolve(), args.output.resolve(), args.patch)
    print(json.dumps({k: v for k, v in result.items() if k != "executions"}))
    return 0 if result["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
