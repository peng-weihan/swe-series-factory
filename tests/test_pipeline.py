"""Real deterministic orchestration with explicitly simulated external adapters.

These tests never claim Docker/model/Oracle execution. Repository acquisition,
AST extraction, specification gates, packaging, repair validation and transaction
commit/rollback run unchanged; only the three external adapters are test doubles.
"""

import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from nl2repo_factory.agents import AgentResponseError
from nl2repo_factory.common import read_json, sha256, write_json
from nl2repo_factory.config import Config
from nl2repo_factory.pipeline import Pipeline
from nl2repo_factory.source import load_records


FIXTURE = Path(__file__).resolve().parents[1] / "nl2repo_factory/fixtures/tagkit"


class FakeAgent:
    """Write real fixture artifacts and report explicitly simulated A1 results."""

    def __init__(self, *, collected=4, fail_first_coverage=False, recover_response=False,
                 repair_actions=None, pollute_tree=False):
        self.collected = collected
        self.fail_first_coverage = fail_first_coverage
        self.recover_response = recover_response
        self.repair_actions = list(repair_actions or [])
        self.pollute_tree = pollute_tree
        self.calls = []
        self.repairs_before = []

    def calls_for(self, role):
        return [call for call in self.calls if call["role"] == role]

    def run(self, role, prompt, inputs, workspace, attempt_dir, timeout):
        self.calls.append({"role": role, "inputs": copy.deepcopy(inputs), "workspace": str(workspace)})
        attempt_dir.mkdir(parents=True, exist_ok=False)
        write_json(attempt_dir / "request.json", {"simulated": True, "role": role, "inputs": inputs})
        (attempt_dir / "prompt.md").write_text(prompt)
        if role == "agent1":
            return {
                "status": "agent1_passed", "simulated": True,
                "docker": {"image": "simulated-dependency-image"},
                "env_spec": {"base_image": "python:3.11-slim", "python_version": "3.11",
                             "install_commands": ["python -m pip install pytest"],
                             "test_commands": ["python -m pytest -q"],
                             "package_files": ["pyproject.toml"], "test_files": ["tests/test_api.py"],
                             "fixture_files": [], "uninstalled_packages": ["tagkit"]},
                "commands": {"oracle_run": "docker run --network none simulated-dependency-image pytest"},
                "oracle_report": {"ok": True, "returncode": 0, "collected": self.collected,
                                  "passed": self.collected, "failed": 0, "errors": 0,
                                  "network_none": True, "stdout_tail": "SIMULATED A1 report; no Docker run"},
            }
        if role == "agent2":
            text = (FIXTURE / "start.md").read_text()
            manifest = read_json(FIXTURE / "api_manifest.json")
            if self.fail_first_coverage and len(self.calls_for("agent2")) == 1:
                text = text.replace("InvalidTagInput", "UndocumentedInput")
                manifest = {"covered_symbols": ["tagkit.normalize_tags"], "core_apis": []}
            if self.pollute_tree:
                text = text.replace("    └── errors.py", "    ├── errors.py\n    └── tests/\n        └── test_api.py")
            start, manifest_path = Path(inputs["start_md_path"]), Path(inputs["api_manifest_path"])
            start.write_text(text)
            write_json(manifest_path, manifest)
            if self.recover_response:
                raise AgentResponseError("simulated malformed final response after valid files")
            return {"status": "stage3_passed", "project_name": "TagKit", "simulated": True,
                    "start_md_path": str(start), "api_manifest_path": str(manifest_path)}
        if role != "repair":
            raise AssertionError(f"Unexpected role: {role}")
        candidate = Path(inputs["candidate_dir"])
        config_path = candidate / "tests/config.json"
        self.repairs_before.append({
            "config": read_json(config_path),
            "api": (candidate / "solution/oracle/tagkit/api.py").read_text(),
            "evidence": read_json(Path(inputs["evidence_dir"]) / "oracle_result.json"),
        })
        action = self.repair_actions.pop(0) if self.repair_actions else "unrepairable"
        changed = []
        if action == "lower_count":
            config = read_json(config_path)
            config["test_case_count"] = 4
            write_json(config_path, config)
            changed = ["tests/config.json"]
        elif action == "mark_reference":
            path = candidate / "solution/oracle/tagkit/api.py"
            path.write_text(path.read_text() + "\n# SIMULATED FAILED CANDIDATE MARKER\n")
            changed = ["solution/oracle/tagkit/api.py"]
        elif action != "unrepairable":
            raise AssertionError(f"Unexpected repair action: {action}")
        return {
            "schema_version": "teamfactory.oracle_repair.v1",
            "status": "repaired" if changed else "unrepairable", "responsible_stage": "stage2_ast",
            "root_cause": "simulated integration-test repair",
            "diagnosis": "Exercise candidate validation and commit/rollback using controlled evidence.",
            "evidence": ["SIMULATED Oracle report; no actual container execution"],
            "changed_files": changed, "image_commands": [],
            "validation_notes": ["Frozen task and test source files are unchanged."],
        }


class FakeDocker:
    """Opaque archive bytes intentionally cannot be loaded as a Docker image."""

    def __init__(self, leak_results=None):
        self.leak_results = list(leak_results or [])
        self.builds = []
        self.saves = []
        self.loads = []
        self.images = {}

    def preflight(self):
        return {"simulated": True, "server_version": "NO_DOCKER_EXECUTION"}

    def build(self, context, tag, log_dir, dockerfile=None):
        self.builds.append({"context": str(context), "tag": tag, "start": (context / "start.md").read_bytes()})
        payload = b"SIMULATED NON-DOCKER ARCHIVE\n" + tag.encode() + b"\n" + (context / "Dockerfile").read_bytes()
        self.images[tag] = {"payload": payload, "start_sha": sha256(context / "start.md")}
        result = {"simulated": True, "id": "sha256:" + hashlib.sha256(payload).hexdigest(), "repo_digests": []}
        write_json(log_dir / "build.json", result)
        return result

    def leak_check(self, tag, log_dir, expected_start_sha256=None):
        if expected_start_sha256 != self.images[tag]["start_sha"]:
            raise AssertionError("Controller built different start.md bytes than it validated")
        passed = self.leak_results.pop(0) if self.leak_results else True
        report = {"simulated": True, "passed": passed, "fatal_count": 0 if passed else 1,
                  "hits": [] if passed else [{"reason": "visible_forbidden_source_path", "path": "repo/leak.py"}],
                  "warnings": [], "start_md_sha256": expected_start_sha256}
        write_json(log_dir / "leak_check.json", report)
        return report

    def save(self, tag, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.images[tag]["payload"])
        self.saves.append({"tag": tag, "destination": str(destination)})

    def load(self, archive):
        if not archive.read_bytes().startswith(b"SIMULATED NON-DOCKER ARCHIVE"):
            raise AssertionError("Unexpected fixture archive")
        self.loads.append(str(archive))


def simulated_report(passed=4, failed=0, errors=0, configured=4):
    observed = passed + failed + errors
    total = max(configured, observed)
    return {"passed": passed, "failed": failed, "errors": errors, "total": total,
            "configured_total": configured, "observed_total": observed,
            "pytest_collected": observed, "reward": passed / total if total else 0}


class FakeOracle:
    """Return controlled trial artifacts clearly marked as simulations."""

    def __init__(self, outcomes=None, *, empty_reward=0):
        self.outcomes = list(outcomes or [])
        self.empty_reward = empty_reward
        self.calls = []

    def run(self, instance, archive, job_dir, *, mode="oracle"):
        config = read_json(instance / "tests/config.json")
        self.calls.append({"mode": mode, "config": copy.deepcopy(config), "image": archive.read_bytes(),
                           "instance": str(instance), "job_dir": str(job_dir)})
        trial = job_dir / "trial"
        verifier = trial / "verifier"
        verifier.mkdir(parents=True, exist_ok=False)
        report = simulated_report(configured=config["test_case_count"]) if mode == "oracle" else simulated_report(passed=0, errors=4, configured=config["test_case_count"])
        reward = report["reward"] if mode == "oracle" else self.empty_reward
        outcome = self.outcomes.pop(0) if self.outcomes else {}
        report = copy.deepcopy(outcome.get("report", report))
        reward = outcome.get("reward", reward)
        status = outcome.get("status") or ("passed" if reward is not None and reward >= 0.999999 else "failed")
        result = {"simulated": True, "evidence_kind": "unit-test-double", "status": status,
                  "reward": reward, "report": report, "trial_dir": str(trial), "mode": mode,
                  "stdout": "SIMULATED Oracle result; no container or test execution"}
        write_json(verifier / "report.json", report)
        (verifier / "test-stdout.txt").write_text(result["stdout"])
        (verifier / "reward.txt").write_text(str(reward) + "\n")
        write_json(trial / "result.json", {"simulated": True, "verifier_result": {"rewards": {"reward": reward}}})
        write_json(job_dir / "oracle_result.json", result)
        return result


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nl2repo-pipeline-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = Config(root=self.root / "runs", dataset_root=self.root / "dataset", image_root=self.root / "images",
                             agent1_retries=0, coverage_retries=1, image_leak_retries=1,
                             oracle_infra_retries=1, repair_rounds=2)
        self.input_file = self.root / "candidates.jsonl"

    def records(self, source=None, duplicate=False):
        record = {"full_name": "synthetic/tagkit", "local_path": str(source or FIXTURE / "repo")}
        self.input_file.write_text(json.dumps(record) + "\n" + (json.dumps(record) + "\n" if duplicate else ""))
        return load_records(self.input_file)

    def execute(self, *, agent=None, docker=None, oracle=None, records=None):
        agent, docker, oracle = agent or FakeAgent(), docker or FakeDocker(), oracle or FakeOracle()
        pipeline = Pipeline(self.config, agent=agent, docker=docker, oracle=oracle)
        selected = records if records is not None else self.records()
        summary = pipeline.run(selected)
        self.pipeline, self.agent, self.docker, self.oracle = pipeline, agent, docker, oracle
        self.task = selected[0]["task_id"]
        self.instance = self.config.dataset_root / self.task
        self.archive = self.config.image_root / (self.task + ".tar")
        self.work = pipeline.run_dir / "work" / self.task
        return summary

    def test_success_packages_real_fixture_and_runs_empty_control(self):
        result = self.execute(agent=FakeAgent(pollute_tree=True))
        self.assertEqual(result["counts"], {"accepted": 1})
        self.assertEqual([call["mode"] for call in self.oracle.calls], ["oracle", "empty"])
        self.assertTrue((self.instance / "solution/oracle/tagkit/api.py").is_file())
        self.assertTrue((self.instance / "tests/reference/tests/test_api.py").is_file())
        self.assertFalse((self.instance / "tests/reference/tagkit/api.py").exists())
        metadata = read_json(self.instance / "build_metadata.json")
        self.assertTrue(metadata["accepted"])
        self.assertTrue(metadata["empty_control_run"])
        self.assertEqual(metadata["image_archive_sha256"], sha256(self.archive))
        final_text = (self.instance / "environment/start.md").read_bytes()
        self.assertEqual(self.docker.builds[0]["start"], final_text)
        self.assertNotIn(b"test_api.py", final_text)
        self.assertEqual(read_json(self.work / "cycle-0/ast.json")["summary"]["implementation_python_file_count"], 5)
        self.assertIsNone(read_json(self.work / "cycle-0/source.json")["commit"])

    def test_empty_answer_full_score_prevents_acceptance(self):
        result = self.execute(oracle=FakeOracle(empty_reward=1))
        self.assertEqual(result["counts"], {"empty_answer_passed": 1})
        self.assertFalse(read_json(self.instance / "build_metadata.json")["accepted"])

    def test_skip_oracle_is_explicitly_unverified(self):
        self.config.skip_oracle = True
        result = self.execute()
        self.assertEqual(result["counts"], {"built_unverified": 1})
        self.assertEqual(self.oracle.calls, [])
        self.assertFalse(read_json(self.instance / "build_metadata.json")["accepted"])

    def test_coverage_failure_is_fed_back_and_regenerated(self):
        result = self.execute(agent=FakeAgent(fail_first_coverage=True))
        self.assertEqual(result["counts"], {"accepted": 1})
        calls = self.agent.calls_for("agent2")
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0]["inputs"]["coverage_feedback"])
        self.assertEqual(calls[1]["inputs"]["coverage_feedback"]["missing_symbols"], ["tagkit.errors.InvalidTagInput"])
        self.assertTrue(read_json(self.work / "cycle-0/coverage-1.json")["passed"])

    def test_valid_files_recover_from_unparseable_a2_response(self):
        result = self.execute(agent=FakeAgent(recover_response=True))
        self.assertEqual(result["counts"], {"accepted": 1})
        self.assertEqual(len(self.agent.calls_for("agent2")), 1)

    def test_leak_restarts_a1_ast_and_a2_with_feedback(self):
        result = self.execute(docker=FakeDocker(leak_results=[False, True]))
        self.assertEqual(result["counts"], {"accepted": 1})
        a1_calls = self.agent.calls_for("agent1")
        self.assertEqual(len(a1_calls), 2)
        self.assertEqual(len(self.agent.calls_for("agent2")), 2)
        self.assertEqual(a1_calls[1]["inputs"]["leak_feedback"]["fatal_count"], 1)
        self.assertTrue((self.work / "cycle-1/ast.json").exists())
        self.assertEqual(len(self.docker.saves), 1)

    def test_initial_infrastructure_retry_exhaustion_does_not_enter_repair(self):
        infra = {"status": "infrastructure_failed", "reward": None}
        result = self.execute(oracle=FakeOracle(outcomes=[infra, infra]))
        self.assertEqual(result["counts"], {"infrastructure_failed": 1})
        self.assertEqual(len(self.oracle.calls), 2)
        self.assertEqual(self.agent.calls_for("repair"), [])
        self.assertFalse(read_json(self.instance / "build_metadata.json")["accepted"])

    def test_transient_infrastructure_failure_can_recover(self):
        result = self.execute(oracle=FakeOracle(outcomes=[{"status": "infrastructure_failed", "reward": None}]))
        self.assertEqual(result["counts"], {"accepted": 1})
        self.assertEqual([call["mode"] for call in self.oracle.calls], ["oracle", "oracle", "empty"])

    def test_trusted_all_passed_report_allows_denominator_repair_and_commit(self):
        result = self.execute(agent=FakeAgent(collected=8, repair_actions=["lower_count"]))
        self.assertEqual(result["counts"], {"accepted": 1})
        self.assertEqual(read_json(self.instance / "tests/config.json")["test_case_count"], 4)
        self.assertEqual([call["config"]["test_case_count"] for call in self.oracle.calls], [8, 4, 4])
        self.assertEqual(read_json(self.work / "repairs/0/transaction.json")["state"], "committed")
        self.assertEqual((self.instance / "environment/start.md").read_bytes(), (FIXTURE / "start.md").read_bytes())
        self.assertTrue(read_json(self.instance / "build_metadata.json")["accepted"])

    def test_failed_candidate_rolls_back_and_cannot_supply_next_trusted_report(self):
        initial = {"status": "failed", "reward": 3 / 8, "report": simulated_report(passed=3, failed=1, configured=8)}
        rejected = {"status": "failed", "reward": 4 / 8, "report": simulated_report(configured=8)}
        result = self.execute(agent=FakeAgent(collected=8, repair_actions=["mark_reference", "lower_count"]),
                              oracle=FakeOracle(outcomes=[initial, rejected]))
        self.assertEqual(result["counts"], {"oracle_failed": 1})
        repairs = self.agent.calls_for("repair")
        self.assertEqual(len(repairs), 2)
        self.assertEqual(repairs[1]["inputs"]["oracle_result"]["report"]["passed"], 3)
        self.assertEqual(repairs[1]["inputs"]["rejected_candidate_feedback"]["report"]["passed"], 4)
        self.assertEqual(self.agent.repairs_before[1]["evidence"]["report"]["passed"], 3)
        self.assertNotIn("SIMULATED FAILED CANDIDATE MARKER", self.agent.repairs_before[1]["api"])
        self.assertEqual(read_json(self.instance / "tests/config.json")["test_case_count"], 8)
        self.assertEqual(self.archive.read_bytes(), self.oracle.calls[0]["image"])
        self.assertEqual(len(self.oracle.calls), 2, "second candidate must fail validation before Oracle")
        self.assertEqual(read_json(self.work / "repairs/0/transaction.json")["state"], "rolled_back")
        self.assertEqual(read_json(self.work / "repair.json")["status"], "rejected")

    def test_repair_infrastructure_exhaustion_restores_original_and_keeps_classification(self):
        initial = {"status": "failed", "reward": 0.5, "report": simulated_report(configured=8)}
        infra = {"status": "infrastructure_failed", "reward": None}
        result = self.execute(agent=FakeAgent(collected=8, repair_actions=["lower_count"]),
                              oracle=FakeOracle(outcomes=[initial, infra, infra]))
        self.assertEqual(result["counts"], {"infrastructure_failed": 1})
        self.assertEqual(read_json(self.instance / "tests/config.json")["test_case_count"], 8)
        self.assertEqual(self.archive.read_bytes(), self.oracle.calls[0]["image"])
        self.assertEqual(read_json(self.work / "repairs/0/transaction.json")["state"], "rolled_back")

    def test_four_implementation_files_are_filtered_before_a2(self):
        source = self.root / "four-file-fixture"
        shutil.copytree(FIXTURE / "repo", source)
        (source / "tagkit/parser.py").unlink()
        result = self.execute(records=self.records(source=source))
        self.assertEqual(result["counts"], {"filtered": 1})
        self.assertEqual(len(self.agent.calls_for("agent1")), 1)
        self.assertEqual(self.agent.calls_for("agent2"), [])
        self.assertEqual(self.docker.builds, [])
        self.assertEqual(self.oracle.calls, [])
        self.assertFalse(self.instance.exists())

    def test_duplicate_identity_is_recorded_without_overwriting_first_task(self):
        result = self.execute(records=self.records(duplicate=True))
        self.assertEqual(result["counts"], {"accepted": 1, "identity_conflict": 1})
        self.assertEqual(len(self.agent.calls_for("agent1")), 1)
        self.assertEqual(len(self.docker.saves), 1)
        self.assertTrue(read_json(self.instance / "build_metadata.json")["accepted"])
        checkpoint = read_json(self.pipeline.run_dir / "checkpoint.json")
        self.assertEqual((checkpoint["completed"], checkpoint["total"]), (2, 2))
        self.assertEqual(len(list(self.config.dataset_root.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
