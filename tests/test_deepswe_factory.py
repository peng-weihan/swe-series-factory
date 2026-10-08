import json
import tempfile
import unittest
from pathlib import Path

from deepswe_factory.demo import BROKEN, FULL, REGRESSION, DemoAgent, run_demo
from deepswe_factory.pipeline import Pipeline
from nl2repo_factory.common import FactoryError, require_process, run_process
from swe_factory.runtime import Runner
from swe_factory.snapshots import copy_tree, diff, initialize


class DeepSWETests(unittest.TestCase):
    def test_all_three_routes_and_packaged_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_demo(root / "demo")
            self.assertTrue(result["passed"])
            self.assertEqual(set(result["routes"]), {"ablation", "query", "specialize"})
            for route in result["routes"].values():
                self.assertEqual((route["nop_reward"], route["oracle_reward"]), (0.0, 1.0))
                self.assertTrue(route["fail_to_pass"])
                self.assertTrue(route["pass_to_pass"])
                self.assertFalse(route["accepted"])
                self.assertFalse(route["outer_harbor_verified"])
            task = Path(result["routes"]["query"]["task_dir"])
            problem = task / "environment/problem"
            tampered = copy_tree(problem, root / "tampered")
            (tampered / "tests/test_regression.py").write_text("def test_plain():\n    assert True\n")
            patch = root / "tampered.patch"
            patch.write_text(diff(problem, tampered))
            check = Runner(backend="host").run(task, ["{python}", "tests/grader.py", "--task", ".", "--output", "verification", "--patch", str(patch)], root / "tampered-check")
            report = json.loads((check["workspace"] / "verification/report.json").read_text())
            self.assertFalse(report["healthy"])
            self.assertEqual(report["reward"], 0.0)
            self.assertIn("protected", " ".join(report["errors"]))

    def test_forward_repair_preserves_query_and_test_version(self):
        class RepairAgent(DemoAgent):
            def __init__(self):
                self.solutions = 0
                self.roles = []

            def run(self, role, prompt, inputs, workspace, attempt_dir, timeout):
                self.roles.append(role)
                result = super().run(role, prompt, inputs, workspace, attempt_dir, timeout)
                if role == "solution":
                    self.solutions += 1
                    if self.solutions == 1:
                        (workspace / "calc.py").write_text(BROKEN + "\n# incomplete first attempt\n")
                return result

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            (source / "tests").mkdir(parents=True)
            (source / "calc.py").write_text(BROKEN)
            (source / "tests/test_regression.py").write_text(REGRESSION)
            commit = initialize(source)
            agent = RepairAgent()
            result = Pipeline(Runner(backend="host"), agent, repair_rounds=1).run(
                {"route": "query", "local_path": str(source), "commit": commit,
                 "test_paths": ["tests"], "production_paths": ["calc.py"]}, root / "run")
            self.assertEqual(result["query_version"], 1)
            self.assertEqual(result["repair_count"], 1)
            self.assertEqual(agent.roles.count("testcase"), 1)
            self.assertEqual(agent.roles.count("query"), 1)
            self.assertEqual(agent.roles.count("solution"), 2)

    def test_mask_cannot_restore_tests_as_solution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = root / "full"
            (full / "tests").mkdir(parents=True)
            (full / "calc.py").write_text(FULL)
            (full / "tests/test_target.py").write_text("original\n")
            problem = copy_tree(full, root / "problem")
            (problem / "calc.py").write_text(BROKEN)
            (problem / "tests/test_target.py").write_text("weakened\n")
            pipeline = Pipeline(Runner(backend="host"))
            pipeline.test_paths, pipeline.production_paths = ["tests"], ["calc.py"]
            with self.assertRaises(FactoryError):
                pipeline.split_mask(full, problem)
