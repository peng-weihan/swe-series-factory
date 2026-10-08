"""Build a tiny real C CLI and run byte-level black-box tests."""
from __future__ import annotations

import base64
import sys
from pathlib import Path

from nl2repo_factory.common import write_json
from swe_factory.runtime import Runner
from swe_factory.snapshots import initialize
from .pipeline import Pipeline

SOURCE = r'''#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--help") == 0) { puts("bytecopy: copy stdin bytes to stdout"); return 0; }
    if (argc != 1) { fputs("unknown option\n", stderr); return 2; }
    int c;
    while ((c = getchar()) != EOF) putchar(c);
    return ferror(stdin) ? 3 : 0;
}
'''


class DemoAgent:
    synthetic = True

    def run(self, role, prompt, inputs, workspace, attempt_dir, timeout):
        attempt_dir.mkdir(parents=True)
        (workspace / "build.sh").write_text("#!/bin/sh\nset -eu\ncc -O0 repo/main.c -o executable\nchmod +x executable\n")
        tests = workspace / "instance/eval/branches/core/tests"
        tests.mkdir(parents=True)
        texts = [b"hello", b"a\n", b"\x00\xff", "中文".encode(), b" ", b"123", b"x" * 1024, b"a\r\nb", b"Z", b"!@#"]
        code = 'import subprocess\nfrom pathlib import Path\nEXE = str(Path("executable").resolve())\n'
        cases = [{"id": "help", "args": ["--help"]}]
        links = {}
        for i, value in enumerate(texts):
            code += f'\ndef test_bytes_{i}():\n    p = subprocess.run([EXE], input={value!r}, capture_output=True, timeout=5)\n    assert p.returncode == 0\n    assert p.stdout == {value!r}\n    assert p.stderr == b""\n'
            cases.append({"id": f"bytes-{i}", "args": [], "stdin_base64": base64.b64encode(value).decode()})
            links[f"core/tests/test_core.py::test_bytes_{i}"] = [f"bytes-{i}"]
        code += '\ndef test_weak_exit():\n    assert subprocess.run([EXE], input=b"", capture_output=True).returncode == 0\n'
        code += '\ndef test_error():\n    p = subprocess.run([EXE,"--invalid"], capture_output=True, timeout=5)\n    assert p.returncode == 2\n    assert p.stderr == b"unknown option\\n"\n'
        cases += [{"id": "empty", "args": []}, {"id": "error", "args": ["--invalid"]}]
        links["core/tests/test_core.py::test_weak_exit"] = ["empty"]
        links["core/tests/test_core.py::test_error"] = ["error"]
        (tests / "test_core.py").write_text(code)
        (workspace / "instance/Dockerfile").write_text("# Synthetic host demo only; R2 recipe intentionally unverified.\nFROM debian:bookworm-slim\n")
        result = {"status": "ok", "eligible": True, "license_file": "LICENSE", "license_reason": "Synthetic fixture created for this repository",
                  "build_command": ["bash", "build.sh"], "public_files": ["LICENSE"], "source_globs": ["*.c"],
                  "core_branches": ["core"], "probe_cases": cases, "test_observations": links,
                  "tool_type": "byte-copy CLI", "summary": "Synthetic binary stdin/stdout and error fixture"}
        write_json(attempt_dir / "synthetic_response.json", result)
        return result


def run_demo(output: Path):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir()
    (source / "main.c").write_text(SOURCE)
    (source / "LICENSE").write_text("Synthetic test fixture. SPDX-License-Identifier: MIT\n")
    commit = initialize(source)
    candidate = {"org": "synthetic", "repo": "bytecopy", "commit": commit, "local_path": str(source), "language": "c", "category": "text-processing"}
    result = Pipeline(Runner(backend="host"), DemoAgent(), rebuild=False).run(candidate, output / "construction")
    result.update(synthetic_fixture=True, model_called=False, docker_executed=False, passed=result["status"] == "qc_passed")
    write_json(output / "summary.json", result)
    return result
