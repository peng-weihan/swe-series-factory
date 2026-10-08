import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nl2repo_factory.common import FactoryError, ProcessResult
from swe_factory.reports import parse_junit, red_green, score
from swe_factory.runtime import Runner, command
from swe_factory.snapshots import apply, changed, copy_tree, diff, files, relative


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_binary_inverse_and_new_file_and_mode(self):
        before = self.root / "before"
        before.mkdir()
        (before / "data.bin").write_bytes(b"\x00old\xff")
        after = copy_tree(before, self.root / "after")
        (after / "data.bin").write_bytes(b"\x00new\xfe")
        (after / "new.py").write_text("print('new')\n")
        (after / "new.py").chmod(0o755)
        forward = diff(before, after)
        replay = copy_tree(before, self.root / "replay")
        self.assertEqual(apply(replay, forward), {"data.bin", "new.py"})
        self.assertEqual(files(replay), files(after))
        apply(replay, diff(after, before))
        self.assertEqual(files(replay), files(before))

    def test_path_traversal_and_symlinks(self):
        for name in ("../escape", "/absolute", ".git/config", "x/../../escape", ""):
            with self.assertRaises(FactoryError):
                relative(name)
        source = self.root / "source"
        source.mkdir()
        (source / "link").symlink_to(self.root / "outside")
        with self.assertRaises(FactoryError):
            copy_tree(source, self.root / "copy")

    def test_no_overwrite(self):
        source = self.root / "source"
        source.mkdir()
        with self.assertRaises(FactoryError):
            copy_tree(source, source)

    def test_directory_replaced_by_file(self):
        before = self.root / "before"
        (before / "entry/sub").mkdir(parents=True)
        (before / "entry/sub/old").write_text("old")
        after = self.root / "after"
        after.mkdir()
        (after / "entry").write_text("replacement")
        replay = copy_tree(before, self.root / "replay")
        apply(replay, diff(before, after))
        self.assertEqual(files(replay), files(after))


class ReportTests(unittest.TestCase):
    def report(self, cases, attributes='errors="0"'):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.xml"
            path.write_text(f'<testsuites><testsuite {attributes}>{cases}</testsuite></testsuites>')
            return parse_junit(path)

    def test_duplicate_worst_and_skip(self):
        report = self.report('<testcase classname="tests.test_api" name="test_x"/>'
                             '<testcase classname="tests.test_api" name="test_x"><failure/></testcase>'
                             '<testcase classname="tests.test_api" name="test_skip"><skipped/></testcase>')
        self.assertEqual(report["tests"]["tests/test_api.py::test_x"], "failed")
        self.assertEqual(score(report["tests"], ["tests/test_api.py::test_skip"], []), 0)

    def test_class_id_from_real_file(self):
        report = self.report('<testcase file="tests/test_api.py" classname="tests.test_api.TestAPI" name="test_x"/>')
        self.assertEqual(list(report["tests"]), ["tests/test_api.py::TestAPI::test_x"])

    def test_collection_error_unhealthy(self):
        report = self.report('<testcase classname="tests.test_api" name="test_x"><error/></testcase>', 'errors="1"')
        self.assertFalse(report["healthy"])

    def test_missing_and_empty_never_green(self):
        self.assertFalse(self.report("")["healthy"])
        self.assertEqual(score({}, ["required"], ["regression"]), 0)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(parse_junit(Path(tmp) / "missing.xml")["healthy"])

    def test_strict_red_green(self):
        before = {"healthy": True, "tests": {"new": "failed", "old": "passed"}}
        after = {"healthy": True, "tests": {"new": "passed", "old": "passed"}}
        self.assertTrue(red_green(before, after)["passed"])
        before["tests"]["new"] = "skipped"
        self.assertFalse(red_green(before, after)["passed"])
        before["tests"].pop("new")
        self.assertFalse(red_green(before, after)["passed"])


class RuntimeTests(unittest.TestCase):
    def test_docker_failure_cleans_only_its_container_and_never_runs_host_command(self):
        class FakeDocker:
            def __init__(self):
                self.calls = []

            def inspect(self, image):
                return {"id": "sha256:fixture"}

            def execute(self, args, **kwargs):
                self.calls.append(args)
                if args[0] == "exec" and args[-1] == "user-command":
                    raise FactoryError("Simulated container transport failure")
                return ProcessResult(args, 0, "", "", False, 0)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "marker").write_text("original")
            runner = Runner(backend="docker", image="configured-image")
            runner.docker = FakeDocker()
            with patch("swe_factory.runtime.run_process") as host:
                with self.assertRaises(FactoryError):
                    runner.run(source, ["user-command"], root / "run")
                host.assert_not_called()
            create = runner.docker.calls[0]
            name = create[create.index("--name") + 1]
            self.assertEqual(runner.docker.calls[-1], ["rm", "-f", name])
            self.assertIn("none", create)
            self.assertEqual((source / "marker").read_text(), "original")

    def test_argv_not_shell(self):
        for value in ("pytest tests", ["bash", "-c", "pytest"], []):
            with self.assertRaises(FactoryError):
                command(value)
        self.assertEqual(command([], empty=True), [])

    def test_real_failure_then_success_have_same_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            path = source / "test_check.py"
            path.write_text("def test_behavior():\n    assert False\n")
            runner = Runner(backend="host")
            before = runner.suite(source, ["{python}", "-m", "pytest", "-q"], root / "before")
            path.write_text("def test_behavior():\n    assert True\n")
            after = runner.suite(source, ["{python}", "-m", "pytest", "-q"], root / "after")
            self.assertTrue(before["healthy"])
            self.assertTrue(after["healthy"])
            self.assertEqual(before["tests"], {"test_check.py::test_behavior": "failed"})
            self.assertEqual(after["tests"], {"test_check.py::test_behavior": "passed"})

    def test_import_failure_is_not_behavioral_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "test_check.py").write_text("import deliberately_missing_dependency_xyz\n")
            report = Runner(backend="host").suite(source, ["{python}", "-m", "pytest", "-q"], root / "run")
            self.assertFalse(report["healthy"])
