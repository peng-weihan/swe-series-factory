"""Self-contained NL2Repo baseline verifier; safe to copy into a task bundle.

The text scoring protocol intentionally retains duplicate counts, summary
fallback, and the absence of an exit-code gate. Path validation and an optional
whole-run deadline protect the execution harness without changing that parser.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import time
from typing import Any


_ITEM = re.compile(r"^(?P<nodeid>[^\s].*?)\s+(?P<status>PASSED|FAILED|ERROR)\s*$")
_SUMMARY = re.compile(r"(\d+)\s+(passed|failed|error|errors)")
_COLLECTED = re.compile(r"\bcollected\s+(\d+)\s+items?\b")


def score_output(output: str, configured_total: int, whitelist: set[str]) -> dict:
    """Implement sections 9.2–9.4 exactly, including baseline quirks."""
    if isinstance(configured_total, bool) or not isinstance(configured_total, int):
        raise ValueError("configured_total must be an integer")
    if configured_total < 0:
        raise ValueError("configured_total must not be negative")
    item_counts = {"passed": 0, "failed": 0, "errors": 0}
    summary_counts = dict(item_counts)
    collected = None
    for line in output.splitlines():
        match = _ITEM.fullmatch(line)
        if match and match["nodeid"].split("::", 1)[0] in whitelist:
            key = match["status"].lower()
            item_counts["errors" if key == "error" else key] += 1
        for match in _SUMMARY.finditer(line):
            key = match[2]
            summary_counts["errors" if key.startswith("error") else key] += int(match[1])
        for match in _COLLECTED.finditer(line):
            collected = int(match[1])
    counts = item_counts if sum(item_counts.values()) else summary_counts
    observed = sum(counts.values())
    total = max(configured_total, observed)
    return {
        **counts,
        "total": total,
        "configured_total": configured_total,
        "observed_total": observed,
        "pytest_collected": collected,
        "reward": min(counts["passed"] / total, 1.0) if total else 0.0,
    }


def relative_asset_path(value: str) -> str:
    """Reject ambiguous, absolute and traversing paths before any filesystem use."""
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError(f"invalid relative asset path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError(f"unsafe relative asset path: {value!r}")
    return path.as_posix()


def test_root_for(path: str) -> str:
    """Baseline test-root heuristic; a *_test.py may select production code."""
    parts = PurePosixPath(relative_asset_path(path)).parts
    for index, part in enumerate(parts[:-1]):
        if part in {"tests", "test"}:
            return "/".join(parts[: index + 1])
    if parts[-1].startswith("test") or parts[-1] == "tests.py":
        return path
    return "/".join(parts[:-1]) if len(parts) > 1 else path


def checked_source(root: Path, relative: str) -> Path:
    root = root.resolve(strict=True)
    source = root / relative_asset_path(relative)
    if not source.resolve(strict=True).is_relative_to(root):
        raise ValueError(f"asset symlink escapes source root: {relative}")
    return source


def _validate_tree(source: Path, root: Path, ancestors: frozenset[Path] = frozenset()) -> None:
    resolved = source.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError(f"asset symlink escapes source root: {source}")
    if resolved in ancestors:
        raise ValueError(f"cyclic source symlink: {source}")
    if source.is_dir():
        for child in source.iterdir():
            _validate_tree(child, root, ancestors | {resolved})
    elif not source.is_file():
        raise ValueError(f"asset is not a regular file or directory: {source}")


def _destination(root: Path, relative: str) -> Path:
    relative = relative_asset_path(relative)
    target = root / relative
    current = root
    for part in PurePosixPath(relative).parts[:-1]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"symlink in destination parent: {current}")
        if current.exists() and not current.is_dir():
            raise ValueError(f"non-directory destination parent: {current}")
    return target


def _remove_target(target: Path) -> None:
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)


def _copy_checked(source: Path, target: Path) -> None:
    """Copy a prevalidated source tree, dereferencing confined source links."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        target.mkdir(exist_ok=True)
        for child in sorted(source.iterdir()):
            _copy_checked(child, target / child.name)
    else:
        shutil.copy2(source, target)


def _run_command(command: str, workspace: Path, deadline: float | None) -> dict:
    remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
    if remaining == 0:
        return {"command": command, "returncode": None, "output": "", "timed_out": True}
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    process = subprocess.Popen(
        command, shell=True, cwd=workspace, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True,
    )
    timed_out = False
    try:
        output, _ = process.communicate(timeout=remaining)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        output, _ = process.communicate()
    return {"command": command, "returncode": process.returncode,
            "output": output, "timed_out": timed_out}


def run_verifier(
    workspace: Path, tests_dir: Path, logs_dir: Path, timeout: float | None = None,
) -> dict:
    """Restore private assets, execute commands and write baseline reward files.

    ``timeout`` is one outer deadline shared by all commands, not a new budget
    for each command. An incomplete timed-out run cannot receive a passing reward.
    """
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be positive")
    deadline = None if timeout is None else time.monotonic() + timeout
    workspace = Path(workspace).resolve(strict=True)
    tests_dir = Path(tests_dir).resolve(strict=True)
    logs_dir = Path(logs_dir).resolve()
    reference = (tests_dir / "reference").resolve(strict=True)
    if not reference.is_relative_to(tests_dir):
        raise ValueError("reference escapes tests directory")
    if workspace == tests_dir or tests_dir.is_relative_to(workspace):
        raise ValueError("private tests directory must be outside workspace")
    config_path = checked_source(tests_dir, "config.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    lists: dict[str, list[str]] = {}
    for name in ("test_files", "package_files", "fixture_files"):
        values = config.get(name, [])
        if not isinstance(values, list):
            raise ValueError(f"{name} must be an array")
        lists[name] = list(dict.fromkeys(relative_asset_path(value) for value in values))
    commands = config.get("test_commands")
    if not isinstance(commands, list) or not commands or any(
        not isinstance(command, str) or not command.strip() for command in commands
    ):
        raise ValueError("test_commands must be a nonempty array of command strings")
    configured = config.get("test_case_count", 0)
    score_output("", configured, set())  # Validate before running any command.
    roots = list(dict.fromkeys(test_root_for(path) for path in lists["test_files"]))
    assets = list(dict.fromkeys(roots + lists["package_files"] + lists["test_files"] + lists["fixture_files"]))
    for relative in assets:
        _validate_tree(checked_source(reference, relative), reference)
    for relative in assets:
        target = _destination(workspace, relative)
        _remove_target(target)  # Never follow a student-controlled target symlink.
        _copy_checked(checked_source(reference, relative), target)
    whitelist = set(lists["test_files"])
    for relative in roots:
        root = checked_source(reference, relative)
        if root.is_dir():
            whitelist.update(path.relative_to(reference).as_posix() for path in root.rglob("*.py"))
        elif root.suffix == ".py":
            whitelist.add(relative)
    # Container-only compatibility alias: host validation must not write /testbed.
    alias = Path("/testbed")
    if workspace == Path("/workspace") and any("/testbed" in command for command in commands):
        if not alias.exists() and not alias.is_symlink():
            alias.symlink_to(workspace, target_is_directory=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with (logs_dir / "test-stdout.txt").open("w", encoding="utf-8") as log:
        for command in commands:
            result = _run_command(command, workspace, deadline)
            results.append(result)
            log.write(f"$ {command}\n{result['output']}\n[returncode={result['returncode']}; timed_out={result['timed_out']}]\n")
            log.flush()
            if result["timed_out"]:
                break
    report = score_output("\n".join(result["output"] for result in results), configured, whitelist)
    report["command_results"] = [{key: value for key, value in result.items() if key != "output"} for result in results]
    report["timed_out"] = any(result["timed_out"] for result in results)
    report["execution_complete"] = not report["timed_out"] and len(results) == len(commands)
    if report["timed_out"]:
        report["parsed_reward"] = report["reward"]
        report["reward"] = 0.0
    (logs_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (logs_dir / "reward.txt").write_text(f"{report['reward']:.10g}\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--tests-dir", type=Path, default=Path("/tests"))
    parser.add_argument("--logs-dir", type=Path, default=Path("/logs/verifier"))
    parser.add_argument("--timeout", type=float, default=None)
    args = parser.parse_args()
    report = run_verifier(args.workspace, args.tests_dir, args.logs_dir, args.timeout)
    print(json.dumps(report))
    return 124 if report["timed_out"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
