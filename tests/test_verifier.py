import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

from nl2repo_factory.verifier import run_verifier, score_output, test_root_for


def python_command(script):
    return shlex.quote(sys.executable) + " -c " + shlex.quote(script)


class ScoreOutputTests(unittest.TestCase):
    def test_all_documented_compatibility_cases(self):
        per_item = "\n".join(f"tests/test_api.py::test_{number} PASSED" for number in range(4))
        cases = [
            (4, "4 passed", (4, 0, 0), 1),
            (4, "3 passed, 1 failed", (3, 1, 0), .75),
            (8, "4 passed", (4, 0, 0), .5),
            (4, "", (0, 0, 0), 0),
            (4, "4 passed, 2 skipped", (4, 0, 0), 1),
            (4, per_item + "\n10 passed", (4, 0, 0), 1),
            (4, "tests/test_api.py::same PASSED\n" * 2, (2, 0, 0), .5),
            (4, "4 passed\n4 passed", (8, 0, 0), 1),
            (0, "1 passed", (1, 0, 0), 1),
            (0, "", (0, 0, 0), 0),
        ]
        for total, output, counts, reward in cases:
            with self.subTest(total=total, output=output):
                report = score_output(output, total, {"tests/test_api.py"})
                self.assertEqual(tuple(report[key] for key in ("passed", "failed", "errors")), counts)
                self.assertEqual(report["reward"], reward)
                self.assertEqual(report["total"], max(total, sum(counts)))

    def test_item_statuses_whitelist_and_summary_precedence(self):
        output = "\n".join([
            "unlisted.py::test PASSED", "tests/test_api.py::a PASSED",
            "tests/test_api.py::b FAILED", "tests/test_api.py::c ERROR", "200 passed",
        ])
        report = score_output(output, 3, {"tests/test_api.py"})
        self.assertEqual((report["passed"], report["failed"], report["errors"]), (1, 1, 1))
        self.assertEqual(report["reward"], 1 / 3)

    def test_progress_suffix_and_nonexact_path_fall_back(self):
        output = "tests/test_api.py::a PASSED [100%]\n./tests/test_api.py::b PASSED\n3 passed, 2 errors"
        report = score_output(output, 4, {"tests/test_api.py"})
        self.assertEqual((report["passed"], report["errors"]), (3, 2))
        self.assertEqual(report["reward"], .6)

    def test_collected_is_last_diagnostic_not_denominator(self):
        report = score_output("collected 100 items\ncollected 99 items\n4 passed", 4, set())
        self.assertEqual(report["pytest_collected"], 99)
        self.assertEqual(report["total"], 4)

    def test_no_skip_or_xfail_counts(self):
        report = score_output("3 skipped, 2 xfailed, 1 xpassed", 4, set())
        self.assertEqual(report["observed_total"], 0)

    def test_invalid_denominator(self):
        for value in (True, -1, "4", 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                score_output("", value, set())

    def test_baseline_test_roots(self):
        expected = {"tests/test_api.py": "tests", "pkg/tests/unit/test_api.py": "pkg/tests",
                    "test_api.py": "test_api.py", "pkg/api_test.py": "pkg"}
        for source, root in expected.items():
            self.assertEqual(test_root_for(source), root)


class VerifierExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.tests = self.root / "private-tests"
        self.reference = self.tests / "reference"
        (self.reference / "tests").mkdir(parents=True)
        (self.reference / "tests/test_api.py").write_text("PRIVATE TEST\n")
        self.logs = self.root / "logs"

    def config(self, commands, **changes):
        config = {"test_case_count": 4, "test_commands": commands,
                  "test_files": ["tests/test_api.py"], "package_files": [], "fixture_files": []}
        config.update(changes)
        (self.tests / "config.json").write_text(json.dumps(config))

    def test_nonzero_exit_continues_and_does_not_override_reward(self):
        self.config([python_command("print('2 passed'); raise SystemExit(1)"),
                     python_command("print('2 passed')")])
        report = run_verifier(self.workspace, self.tests, self.logs)
        self.assertEqual(report["reward"], 1)
        self.assertEqual([row["returncode"] for row in report["command_results"]], [1, 0])
        self.assertEqual((self.logs / "reward.txt").read_text(), "1\n")
        self.assertIn("returncode=1", (self.logs / "test-stdout.txt").read_text())
        self.assertEqual(json.loads((self.logs / "report.json").read_text())["reward"], 1)

    def test_restore_removes_target_symlink_and_preserves_external_content(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "sentinel").write_text("KEEP")
        (self.workspace / "tests").symlink_to(outside, target_is_directory=True)
        self.config([python_command("print('4 passed')")])
        run_verifier(self.workspace, self.tests, self.logs)
        self.assertFalse((self.workspace / "tests").is_symlink())
        self.assertEqual((outside / "sentinel").read_text(), "KEEP")
        self.assertEqual((self.workspace / "tests/test_api.py").read_text(), "PRIVATE TEST\n")

    def test_restore_package_files_and_directory_fixtures(self):
        (self.reference / "pyproject.toml").write_text("private package config")
        (self.workspace / "pyproject.toml").write_text("student config")
        (self.reference / "data").mkdir()
        (self.reference / "data/input.txt").write_text("fixture")
        (self.workspace / "data").mkdir()
        (self.workspace / "data/stale.txt").write_text("remove")
        self.config([python_command("print('4 passed')")], package_files=["pyproject.toml"], fixture_files=["data"])
        run_verifier(self.workspace, self.tests, self.logs)
        self.assertEqual((self.workspace / "pyproject.toml").read_text(), "private package config")
        self.assertFalse((self.workspace / "data/stale.txt").exists())
        self.assertEqual((self.workspace / "data/input.txt").read_text(), "fixture")

    def test_reject_traversal_and_source_symlink_escape(self):
        self.config([python_command("print('4 passed')")], fixture_files=["../outside"])
        with self.assertRaises(ValueError):
            run_verifier(self.workspace, self.tests, self.logs)
        (self.reference / "tests/escape").symlink_to(self.root / "workspace", target_is_directory=True)
        self.config([python_command("print('4 passed')")])
        with self.assertRaises(ValueError):
            run_verifier(self.workspace, self.tests, self.logs)

    def test_reject_symlink_parent_before_overwriting_private_target(self):
        (self.reference / "data").mkdir()
        (self.reference / "data/file.txt").write_text("private")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "file.txt").write_text("KEEP")
        (self.workspace / "data").symlink_to(outside, target_is_directory=True)
        self.config([python_command("print('4 passed')")], fixture_files=["data/file.txt"])
        with self.assertRaises(ValueError):
            run_verifier(self.workspace, self.tests, self.logs)
        self.assertEqual((outside / "file.txt").read_text(), "KEEP")

    def test_outer_timeout_does_not_accept_partial_passing_output(self):
        self.config([python_command("import time; print('4 passed', flush=True); time.sleep(20)")])
        start = time.monotonic()
        report = run_verifier(self.workspace, self.tests, self.logs, timeout=.15)
        self.assertLess(time.monotonic() - start, 4)
        self.assertTrue(report["timed_out"])
        self.assertFalse(report["execution_complete"])
        self.assertEqual(report["reward"], 0)
        self.assertEqual(report["parsed_reward"], 1)

    def test_copied_verifier_runs_without_package_imports(self):
        import nl2repo_factory.verifier as verifier_module
        standalone = self.root / "standalone.py"
        standalone.write_bytes(Path(verifier_module.__file__).read_bytes())
        self.config([python_command("print('4 passed')")])
        result = subprocess.run([sys.executable, "-I", str(standalone), "--workspace", str(self.workspace),
                                 "--tests-dir", str(self.tests), "--logs-dir", str(self.logs)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["reward"], 1)


if __name__ == "__main__":
    unittest.main()
