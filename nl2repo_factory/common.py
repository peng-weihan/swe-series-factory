"""Small filesystem and process primitives shared by execution adapters."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class FactoryError(RuntimeError):
    """An expected, reportable pipeline failure."""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def contained(path: Path, root: Path) -> Path:
    result = path.resolve()
    if not result.is_relative_to(root.resolve()):
        raise FactoryError(f"Path escapes task directory: {path}")
    return result


@dataclass
class ProcessResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    elapsed_sec: float

    def to_dict(self) -> dict:
        return asdict(self)


def run_process(argv: list[str], *, cwd: Path | None = None,
                timeout: float = 3600, stdin: str | None = None,
                env: dict[str, str] | None = None) -> ProcessResult:
    start = time.monotonic()
    try:
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        raise FactoryError(f"Cannot start {argv[0]!r}: {exc}") from exc
    timed_out = False
    try:
        stdout, stderr = process.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
    return ProcessResult(argv, process.returncode, stdout, stderr, timed_out,
                         round(time.monotonic() - start, 3))


def require_process(result: ProcessResult, context: str) -> ProcessResult:
    if result.timed_out:
        raise FactoryError(f"{context} timed out: {result.stderr[-2000:]}")
    if result.returncode != 0:
        raise FactoryError(f"{context} failed ({result.returncode}): {result.stderr[-4000:]} {result.stdout[-1000:]}")
    return result
