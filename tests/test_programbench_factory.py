import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from nl2repo_factory.common import FactoryError
from programbench_factory.actions import ActionAgent, parse_action
from programbench_factory.demo import run_demo
from programbench_factory.quality import branch_id, classify, difficulty, flatten
from programbench_factory.delivery import check_recipe, inspect_cleanroom
from swe_factory.runtime import Runner


class ProgramBenchTests(unittest.TestCase):
    def test_recipe_pin_and_stage_alias(self):
        recipe = "FROM example@sha256:" + "a" * 64 + " AS build\nFROM build AS runtime\n# " + "b" * 40 + "\n# /opt/programbench-source-commit /opt/programbench-stashed-executable-do-not-modify\n"
        check_recipe(recipe, "b" * 40)
        with self.assertRaises(FactoryError):
            check_recipe(recipe.replace("example@sha256:" + "a" * 64, "example:latest"), "b" * 40)

    def test_cleanroom_source_leak_and_gold_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "runtime.tar"
            with tarfile.open(archive, "w") as tar:
                for name, data in (("workspace/executable", b"gold"), ("opt/programbench-stashed-executable-do-not-modify", b"gold"), ("opt/hidden-source", b"original source")):
                    info = tarfile.TarInfo(name)
                    info.size, info.mode, info.uid = len(data), 0o111, 0
                    tar.addfile(info, io.BytesIO(data))
            clean = inspect_cleanroom(archive, {"not-a-real-hash"}, "1000")
            self.assertTrue(clean["passed"])
            leak = inspect_cleanroom(archive, {hashlib.sha256(b"original source").hexdigest()}, "1000")
            self.assertFalse(leak["passed"])
            self.assertFalse(inspect_cleanroom(archive, set(), "0")["passed"])

    def test_branch_hash(self):
        self.assertEqual(branch_id("a" * 40, "core"), hashlib.sha256(("a" * 40 + ":core").encode()).hexdigest()[:12])
        self.assertEqual(branch_id("b" * 40, "123456abcdef"), "123456abcdef")

    def test_flatten_maps_native_and_dotted_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tests = root / "branches/core/tests"
            tests.mkdir(parents=True)
            (tests / "test_core.py").write_text("def test_alpha():\n    assert True\n")
            result = flatten(root / "branches", root / "flat", "a" * 40)
            value = next(iter(result.values()))
            dotted = value["tests"][0]
            self.assertTrue(dotted.startswith("tests.test_"))
            self.assertTrue(value["mapping"][dotted].endswith(".py::test_alpha"))
            self.assertEqual(value["origins"][dotted], "core/tests/test_core.py::test_alpha")

    def test_decorated_tests_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tests = root / "branches/core/tests"
            tests.mkdir(parents=True)
            (tests / "test_core.py").write_text("@decorator\ndef test_alpha():\n    pass\n")
            with self.assertRaises(FactoryError):
                flatten(root / "branches", root / "flat", "a" * 40)

    def test_classification_order_and_skips(self):
        mapping = {f"tests.t.test_{i}": f"tests/t.py::test_{i}" for i in range(12)}
        branches = {"branch": {"tests": list(mapping), "mapping": mapping}}
        dummy = {"healthy": True, "tests": {node: "failed" for node in mapping.values()}}
        gold = {"healthy": True, "tests": {node: "passed" for node in mapping.values()}}
        ids = list(mapping.values())
        dummy["tests"][ids[0]] = "passed"
        dummy["tests"][ids[1]] = "skipped"
        gold["tests"][ids[2]] = "skipped"
        del gold["tests"][ids[3]]
        quality = classify(branches, dummy, gold)
        self.assertTrue(quality["passed"])
        self.assertEqual(quality["n_kept"], 8)
        reasons = [v["reasons"][0]["id"] for v in branches["branch"]["ignored_tests"]]
        self.assertEqual(reasons, ["dummy_pass", "dummy_invalid", "gold_fail", "slow_or_hang"])

    def test_action_parser_and_host_rejection(self):
        self.assertEqual(parse_action('```json\n{"tool":"done"}\n```'), {"tool": "done"})
        with self.assertRaises(FactoryError):
            parse_action('{"tool":"unknown"}')
        with self.assertRaises(FactoryError):
            ActionAgent(Runner(backend="host"), base_url="http://localhost/v1", model="test")

    def test_unrated_difficulty(self):
        self.assertEqual(difficulty("copy bytes", 1, 11)["difficulty"], "unrated")
        text = " ".join("--flag" + str(i) for i in range(50))
        self.assertEqual(difficulty(text, 1, 30)["difficulty"], "hard")

    def test_real_binary_demo_filters_weak_test_without_claiming_r2(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_demo(Path(tmp) / "demo")
            self.assertTrue(result["passed"])
            self.assertEqual(result["quality"]["n_tests"], 12)
            self.assertEqual(result["quality"]["n_kept"], 11)
            self.assertFalse(result["r2_verified"])
            self.assertFalse(result["model_called"])
            self.assertFalse(result["published"])
