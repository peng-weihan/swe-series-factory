"""Standalone byte-preserving reference observation runner."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


def b64(value):
    return base64.b64encode(value).decode("ascii")


def observe(root: Path, cases: list[dict]):
    executable = (root / "executable").resolve()
    binary_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    observations = []
    for index, case in enumerate(cases):
        for repeat in (0, 1):
            cwd = root / "probes" / f"{index}-{repeat}"
            cwd.mkdir(parents=True, exist_ok=False)
            for name, data in case.get("input_files", {}).items():
                target = (cwd / name).resolve()
                if not target.is_relative_to(cwd.resolve()):
                    raise ValueError("Probe fixture escapes its workspace")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(data, validate=True))
            env = dict(os.environ, TZ="UTC", LC_ALL="C")
            env.update(case.get("env", {}))
            argv = [str(executable), *case["args"]]
            process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=env, start_new_session=True)
            timed_out = False
            try:
                stdout, stderr = process.communicate(base64.b64decode(case.get("stdin_base64", ""), validate=True), timeout=case.get("timeout", 30))
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            output_files = {}
            for path in sorted(cwd.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Probe produced a symlink")
                if path.is_file():
                    output_files[path.relative_to(cwd).as_posix()] = b64(path.read_bytes())
            observations.append({"probe_id": case["id"], "repeat": repeat, "gold_sha256": binary_hash,
                                 "argv": case["args"], "stdin_base64": case.get("stdin_base64", ""),
                                 "env": case.get("env", {}), "input_files": case.get("input_files", {}),
                                 "exit_code": process.returncode, "stdout_base64": b64(stdout), "stderr_base64": b64(stderr),
                                 "output_files": output_files, "timed_out": timed_out, "normalization": []})
    (root / "observations.json").write_text(json.dumps(observations, indent=2) + "\n")


if __name__ == "__main__":
    observe(Path.cwd(), json.loads(Path(sys.argv[1]).read_text()))
