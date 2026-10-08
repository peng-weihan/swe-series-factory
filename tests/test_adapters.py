"""Adapter contracts exercised without a model service or Docker daemon."""

import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import time
import unittest

from nl2repo_factory.agents import AgentResponseError, CommandAgent, parse_response
from nl2repo_factory.common import FactoryError
from nl2repo_factory.config import Config
from nl2repo_factory.contracts import extract_reward, normalize_reward, validate_a1
from nl2repo_factory.docker import inspect_filesystem


def a1_response():
    return {
        "status": "agent1_passed",
        "docker": {"image": "dependency-image"},
        "env_spec": {"install_commands": [], "test_commands": ["python -m pytest -q"],
                     "test_files": ["tests/test_api.py"]},
        "commands": {"oracle_run": "docker run --network none dependency-image python -m pytest -q"},
        "oracle_report": {"ok": True, "returncode": 0, "passed": 4, "failed": 0,
                          "errors": 0, "collected": 4, "network_none": True},
    }


class ContractTests(unittest.TestCase):
    def test_a1_offline_nonempty_success_and_equals_network_syntax(self):
        result = a1_response()
        self.assertTrue(validate_a1(result)["passed"])
        result["commands"]["oracle_run"] = "docker run --network=none dependency-image pytest"
        self.assertTrue(validate_a1(result)["passed"])

    def test_a1_rejects_empty_tests_bool_counts_and_inconsistent_success(self):
        mutations = [
            ("oracle_report", "collected", 0),
            ("oracle_report", "passed", True),
            ("oracle_report", "failed", 1),
            ("oracle_report", "returncode", 1),
            ("oracle_report", "ok", 1),
            ("oracle_report", "network_none", False),
            ("env_spec", "test_commands", []),
            ("env_spec", "test_files", []),
            ("commands", "oracle_run", "docker run --network none-extra dependency-image pytest"),
            ("docker", "image", ""),
        ]
        for section, field, value in mutations:
            with self.subTest(section=section, field=field, value=value):
                result = a1_response()
                result[section][field] = value
                self.assertFalse(validate_a1(result)["passed"])

    def test_a1_requires_objects_and_an_explicit_success_status(self):
        result = a1_response()
        result["env_spec"] = []
        self.assertFalse(validate_a1(result)["passed"])
        result = a1_response()
        result["status"] = "agent1_failed"
        self.assertFalse(validate_a1(result)["passed"])
        self.assertFalse(validate_a1({})["passed"])

    def test_reward_normalization_preserves_missing_and_rejects_nonfinite(self):
        for value, expected in [
            (1, 1.0), (0.75, 0.75), ({"reward": 0.5}, 0.5),
            ({"score": 0.25}, 0.25), ({"mean": 1}, 1.0),
            ({"metric": 0.3, "description": "single numeric member"}, 0.3),
            ({"score": {"mean": 0.7}}, 0.7),
        ]:
            with self.subTest(value=value):
                self.assertEqual(normalize_reward(value), expected)
        for value in [None, True, False, "1", [], {}, {"a": 1, "b": 0.5},
                      float("nan"), float("inf"), {"reward": float("nan")}]:
            with self.subTest(value=value):
                self.assertIsNone(normalize_reward(value))

    def test_reward_extraction_unwraps_trial_protocol(self):
        self.assertEqual(extract_reward({"verifier_result": {"rewards": {"reward": 0.75}}}), 0.75)
        self.assertEqual(extract_reward({"rewards": {"score": 1}}), 1)
        self.assertIsNone(extract_reward({"verifier_result": {"rewards": None}}))
        self.assertIsNone(extract_reward({"verifier_result": {"rewards": {"reward": True}}}))


class ResponseParserTests(unittest.TestCase):
    def test_plain_prose_fenced_and_cli_envelope(self):
        response = {"status": "stage3_passed", "start_md_path": "stage3/start.md"}
        raw = json.dumps(response)
        for text in [raw, "Done.\n" + raw, "```json\n" + raw + "\n```",
                     json.dumps({"result": raw, "cost": 0.01})]:
            with self.subTest(text=text):
                self.assertEqual(parse_response(text), response)

    def test_nested_status_cannot_replace_outer_response(self):
        response = a1_response()
        response["oracle_report"]["status"] = "passed"
        response["notes"] = {"status": "diagnostic", "text": "nested object"}
        self.assertEqual(parse_response("Completed:\n" + json.dumps(response)), response)

    def test_last_complete_top_level_response_wins(self):
        older = {"status": "stage3_failed", "notes": {"status": "diagnostic"}}
        final = {"status": "stage3_passed", "notes": {"status": "diagnostic"}}
        self.assertEqual(parse_response(json.dumps(older) + "\nCorrected result:\n" + json.dumps(final)), final)

    def test_unparseable_response_is_distinct_from_process_failure(self):
        for text in ["Task finished without JSON", "```json\n{broken\n```", "[]"]:
            with self.subTest(text=text):
                with self.assertRaises(AgentResponseError):
                    parse_response(text)


class CommandAgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nl2repo adapter ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace with spaces"
        self.attempt = self.root / "attempt"

    def test_real_child_receives_request_prompt_and_writes_response_file(self):
        script = '''import json, os, pathlib, sys
request, response, role, workspace, prompt_path = sys.argv[1:]
data = json.loads(pathlib.Path(request).read_text())
prompt = sys.stdin.read()
assert prompt == data["prompt"] == pathlib.Path(prompt_path).read_text()
assert role == os.environ["NL2REPO_ROLE"] == data["role"]
assert pathlib.Path.cwd() == pathlib.Path(workspace)
assert os.environ["ADAPTER_TEST_VALUE"] == "expected"
assert data["inputs"] == {"example": 7}
pathlib.Path(response).write_text(json.dumps({"status": "stage3_passed", "prompt": prompt}))
print("stdout is a trace, not the response")
print("stderr is retained", file=sys.stderr)
'''
        agent = CommandAgent([sys.executable, "-c", script, "{request}", "{response}", "{role}", "{workspace}", "{prompt}"],
                             env={"ADAPTER_TEST_VALUE": "expected"})
        prompt = "实施测试：keep literal $HOME and `commands` unchanged.\n"
        result = agent.run("agent2", prompt, {"example": 7}, self.workspace, self.attempt, 5)
        self.assertEqual(result, {"status": "stage3_passed", "prompt": prompt})
        self.assertEqual(json.loads((self.attempt / "parsed_response.json").read_text()), result)
        self.assertIn("trace", (self.attempt / "stdout.txt").read_text())
        self.assertIn("retained", (self.attempt / "stderr.txt").read_text())
        self.assertEqual(json.loads((self.attempt / "execution.json").read_text())["returncode"], 0)

    def test_stdout_fallback_with_prompt_stdin_disabled(self):
        script = 'import json, sys; assert sys.stdin.read() == ""; print(json.dumps({"status": "agent1_passed"}))'
        agent = CommandAgent([sys.executable, "-c", script], stdin_prompt=False)
        result = agent.run("agent1", "stored but not on stdin", {}, self.workspace, self.attempt, 5)
        self.assertEqual(result["status"], "agent1_passed")
        self.assertFalse((self.attempt / "response.json").exists())

    def test_nonzero_exit_rejects_even_a_written_success_response(self):
        script = '''import json, os, pathlib, sys
pathlib.Path(os.environ["NL2REPO_RESPONSE"]).write_text(json.dumps({"status": "agent1_passed"}))
print("intentional child error", file=sys.stderr)
sys.exit(7)
'''
        agent = CommandAgent([sys.executable, "-c", script])
        with self.assertRaisesRegex(FactoryError, "exited 7"):
            agent.run("agent1", "prompt", {}, self.workspace, self.attempt, 5)
        execution = json.loads((self.attempt / "execution.json").read_text())
        self.assertEqual(execution["returncode"], 7)
        self.assertFalse(execution["timed_out"])
        self.assertIn("intentional child error", (self.attempt / "stderr.txt").read_text())

    def test_timeout_terminates_child_and_retains_execution_evidence(self):
        agent = CommandAgent([sys.executable, "-c", 'import time; print("started", flush=True); time.sleep(10)'])
        started = time.monotonic()
        with self.assertRaisesRegex(FactoryError, "timed out"):
            agent.run("agent2", "prompt", {}, self.workspace, self.attempt, 0.3)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(json.loads((self.attempt / "execution.json").read_text())["timed_out"])
        self.assertIn("started", (self.attempt / "stdout.txt").read_text())

    def test_invalid_output_preserves_raw_trace_for_recovery(self):
        agent = CommandAgent([sys.executable, "-c", 'print("Files are ready, but no response JSON")'])
        with self.assertRaises(AgentResponseError):
            agent.run("agent2", "prompt", {}, self.workspace, self.attempt, 5)
        self.assertIn("Files are ready", (self.attempt / "stdout.txt").read_text())
        self.assertTrue((self.attempt / "execution.json").exists())


class ImageFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive = Path(self.temporary.name) / "filesystem.tar"

    def make_tar(self, files, symlinks=None):
        with tarfile.open(self.archive, "w") as archive:
            for name, content in files.items():
                content = content.encode() if isinstance(content, str) else content
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            for name, target in (symlinks or {}).items():
                member = tarfile.TarInfo(name)
                member.type = tarfile.SYMTYPE
                member.linkname = target
                archive.addfile(member)

    def test_forbidden_source_roots_are_fatal_and_hits_are_bounded(self):
        files = {f"{root}/module.py": "reference implementation" for root in ("app", "repo", "src", "testbed")}
        files.update({f"repo/module_{number}.py": "pass" for number in range(51)})
        self.make_tar(files)
        report = inspect_filesystem(self.archive)
        self.assertFalse(report["passed"])
        self.assertEqual(report["fatal_count"], 55)
        self.assertEqual(len(report["hits"]), 50)
        self.assertTrue(all(hit["reason"] == "visible_forbidden_source_path" for hit in report["hits"]))

    def test_local_install_metadata_distinguishes_editable_warning(self):
        base = "usr/local/lib/python3.11/site-packages/"
        self.make_tar({
            base + "ordinary.dist-info/direct_url.json": json.dumps({"url": "file:///repo"}),
            base + "editable.dist-info/direct_url.json": json.dumps({"url": "file:///repo", "dir_info": {"editable": True}}),
            base + "remote.dist-info/direct_url.json": json.dumps({"url": "https://example.invalid/package.whl"}),
            base + "invalid.dist-info/direct_url.json": "{broken",
            base + "local.pth": "/workspace/repo\n",
        })
        report = inspect_filesystem(self.archive)
        self.assertEqual(report["fatal_count"], 1)
        self.assertEqual(report["hits"][0]["reason"], "noneditable_file_direct_url")
        self.assertEqual({row["reason"] for row in report["warnings"]},
                         {"editable_file_direct_url", "invalid_direct_url", "pth_source_reference"})

    def test_start_document_hash_matches_actual_bytes_and_missing_is_fatal(self):
        document = "## Task\n原始题面\n".encode()
        expected = hashlib.sha256(document).hexdigest()
        self.make_tar({"./workspace/start.md": document, "usr/local/lib/dependency.py": "pass"})
        report = inspect_filesystem(self.archive, expected)
        self.assertTrue(report["passed"])
        self.assertEqual(report["start_md_sha256"], expected)
        self.assertFalse(inspect_filesystem(self.archive, "0" * 64)["passed"])
        self.make_tar({"workspace/unrelated.md": document})
        missing = inspect_filesystem(self.archive, expected)
        self.assertEqual(missing["hits"][0]["reason"], "start_md_digest_mismatch")
        self.assertIsNone(missing["start_md_sha256"])

    def test_scope_is_regular_files_only_and_no_archive_is_extracted(self):
        self.make_tar({"opt/reference.py": "outside documented roots", "repo/readme.txt": "not Python"},
                      symlinks={"repo/link.py": "/host/private-file", "workspace/start.md": "/etc/passwd"})
        report = inspect_filesystem(self.archive)
        self.assertTrue(report["passed"])
        self.assertIn("not a proof", report["scope"])
        self.assertFalse((self.archive.parent / "repo").exists())
        self.assertFalse(inspect_filesystem(self.archive, "0" * 64)["passed"])

    def test_oversized_install_metadata_is_rejected(self):
        self.make_tar({"usr/lib/site-packages/large.dist-info/direct_url.json": b" " * (1024 * 1024 + 1)})
        report = inspect_filesystem(self.archive)
        self.assertEqual(report["hits"][0]["reason"], "oversized_install_metadata")


class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "factory.toml"

    def load(self, extra="", agent_extra=""):
        self.path.write_text('[paths]\nroot = "runs"\ndataset = "datasets"\nimages = "images"\n'
                             '[agent]\nargv = ["configured-cli"]\n' + agent_extra + '\n[pipeline]\n' + extra)
        return Config.load(self.path)

    def test_paths_resolve_from_config_and_zero_repair_rounds_normalize(self):
        result = self.load("repair_rounds = 0\nverify_empty = false\n")
        self.assertEqual(result.dataset_root, (self.path.parent / "datasets").resolve())
        self.assertEqual(result.repair_rounds, 1)
        self.assertFalse(result.verify_empty)

    def test_bool_options_reject_strings_and_numbers(self):
        for option in ['skip_oracle = "false"', 'verify_empty = 0']:
            with self.subTest(option=option):
                with self.assertRaises(ValueError):
                    self.load(option)
        with self.assertRaises(ValueError):
            self.load(agent_extra='stdin_prompt = "false"')

    def test_nonfinite_and_nonpositive_timeouts_are_invalid(self):
        for value in ["nan", "inf", "-inf", "0", "-1", "true"]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.load("agent_timeout = " + value)


if __name__ == "__main__":
    unittest.main()
