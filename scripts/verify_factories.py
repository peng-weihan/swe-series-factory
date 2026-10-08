"""Run the local suite and retain an honest, machine-readable verification record."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import unittest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    args.output.mkdir(parents=True, exist_ok=False)
    expected = {
        "nl2repo.md": "d1283fc5ef3549a3bba762b4e8b990e440b350d7aaa1a409d2b38ca42b2cfc0d",
        "deepswe.md": "fdbcd876402f488a5579c370312fd8d8e8e0f3c543e26b8c5611648829081565",
        "programbench.md": "0cdac59c3d6edb32d9193f4157f33ab3e7e4f9a23b8f31beff4208d4426b1966",
    }
    specs = {}
    for name, checksum in expected.items():
        path = root / name
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        specs[name] = {"sha256": actual, "unchanged": actual == checksum, "read_only": path.stat().st_mode & 0o222 == 0}
    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    with (args.output / "unittest.log").open("w") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {"passed": result.wasSuccessful() and all(v["unchanged"] and v["read_only"] for v in specs.values()),
              "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "python": platform.python_version(), "source_specs": specs,
              "real_model_called": False, "real_docker_executed": False,
              "test_scope": "unit/adapters plus real host synthetic construction, compiler, pytest and packaged-verifier replay"}
    (args.output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
