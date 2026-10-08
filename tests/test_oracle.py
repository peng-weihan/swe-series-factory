"""Adapter contract tests using a fake Docker transport, never real containers."""

import json
from pathlib import Path
import tempfile
import unittest

from nl2repo_factory.common import FactoryError, ProcessResult
from nl2repo_factory.oracle import DockerOracle


_ABSENT = object()


class FakeDocker:
    """Record transport calls and materialize pretend container logs on download."""

    def __init__(self, *, reward="1\n", report=_ABSENT, start_code=0,
                 timed_out=False, stdout="", stderr="", load_error=None,
                 cleanup_error=None, cleanup_code=0, download_code=0,
                 create_code=0, download_stderr="container log path not found"):
        self.calls = []
        self.reward = reward
        self.report = {"reward": 1.0, "timed_out": False} if report is _ABSENT else report
        self.start_code = start_code
        self.timed_out = timed_out
        self.stdout = stdout
        self.stderr = stderr
        self.load_error = load_error
        self.cleanup_error = cleanup_error
        self.cleanup_code = cleanup_code
        self.download_code = download_code
        self.download_stderr = download_stderr
        self.create_code = create_code

    def load(self, archive):
        self.calls.append((["load", str(archive)], None))
        if self.load_error:
            raise self.load_error

    def execute(self, arguments, *, timeout=None):
        arguments = list(arguments)
        self.calls.append((arguments, timeout))
        code, stdout, stderr, timed_out = 0, "", "", False
        if arguments[0] == "create":
            code = self.create_code
            stdout = "fake-container-id\n" if not code else ""
            stderr = "container creation failed" if code else ""
        elif arguments[0] == "start":
            code, stdout, stderr, timed_out = self.start_code, self.stdout, self.stderr, self.timed_out
        elif arguments[0] == "cp" and ":/logs/verifier/." in arguments[1]:
            code = self.download_code
            if code:
                stderr = self.download_stderr
            else:
                destination = Path(arguments[2])
                destination.mkdir(parents=True, exist_ok=True)
                if self.reward is not None:
                    (destination / "reward.txt").write_text(self.reward)
                if self.report is not _ABSENT:
                    (destination / "report.json").write_text(json.dumps(self.report))
                (destination / "test-stdout.txt").write_text("simulated test output\n")
        elif arguments[0] == "rm":
            if self.cleanup_error:
                raise self.cleanup_error
            code = self.cleanup_code
            stderr = "cleanup failed" if code else ""
        return ProcessResult(arguments, code, stdout, stderr, timed_out, .01)


class DockerOracleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.instance = self.root / "tagkit-instance"
        (self.instance / "environment").mkdir(parents=True)
        (self.instance / "environment/start.md").write_text("# TagKit\n")
        (self.instance / "tests").mkdir()
        (self.instance / "tests/test.sh").write_text("#!/bin/bash\n")
        (self.instance / "solution").mkdir()
        (self.instance / "solution/solve.sh").write_text("#!/bin/bash\n")
        (self.instance / "task.toml").write_text(
            'version = "1.0"\n[environment]\ncpus = 2\nmemory_mb = 8192\n')
        (self.instance / "build_metadata.json").write_text(json.dumps({"image_tag": "recorded-tag:latest"}))
        self.archive = self.root / "image.tar"
        self.archive.write_bytes(b"FAKE ARCHIVE - not Docker data")
        self.job_number = 0

    def run_oracle(self, docker=None, *, mode="oracle"):
        docker = docker or FakeDocker()
        self.job_number += 1
        job = self.root / f"job-{self.job_number}"
        result = DockerOracle(docker, timeout=17).run(self.instance, self.archive, job, mode=mode)
        return result, docker, job

    def test_oracle_uses_copy_transport_and_only_oracle_receives_solution(self):
        result, docker, job = self.run_oracle()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["reward"], 1)
        calls = [arguments for arguments, _ in docker.calls]
        create = next(arguments for arguments in calls if arguments[0] == "create")
        self.assertEqual(create[create.index("--network") + 1], "none")
        self.assertEqual(create[create.index("--cpus") + 1], "2")
        self.assertEqual(create[create.index("--memory") + 1], "8192m")
        self.assertEqual(create[create.index("--entrypoint") + 1], "/bin/bash")
        self.assertEqual(create[create.index("--workdir") + 1], "/workspace")
        self.assertEqual(create[-3:], ["recorded-tag:latest", "-c", "bash /solution/solve.sh && bash /tests/test.sh"])
        self.assertNotIn("-lc", create)
        self.assertFalse(any("--mount" in arguments or "--volume" in arguments or "-v" in arguments for arguments in calls))
        uploads = [arguments for arguments in calls if arguments[0] == "cp" and ":/" in arguments[2]]
        self.assertEqual([arguments[2].split(":", 1)[1] for arguments in uploads], ["/workspace", "/tests", "/solution"])
        self.assertTrue(uploads[0][1].endswith("/workspace/."))
        self.assertTrue(uploads[1][1].endswith("/instance/tests"))
        self.assertTrue(uploads[2][1].endswith("/instance/solution"))
        self.assertEqual(next(timeout for arguments, timeout in docker.calls if arguments[0] == "start"), 17)
        self.assertEqual(calls[0], ["load", str(self.archive)])
        self.assertEqual(calls[-1][0:2], ["rm", "-f"])
        self.assertTrue((job / "trial/verifier/test-stdout.txt").exists())
        stored = json.loads((job / "trial/result.json").read_text())
        self.assertEqual(stored["verifier_result"]["rewards"]["reward"], 1)
        self.assertEqual(json.loads((job / "oracle_result.json").read_text())["status"], "passed")

    def test_empty_control_never_uploads_or_executes_solution(self):
        result, docker, _ = self.run_oracle(FakeDocker(reward="0\n"), mode="empty")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["mode"], "empty")
        calls = [arguments for arguments, _ in docker.calls]
        create = next(arguments for arguments in calls if arguments[0] == "create")
        self.assertEqual(create[-1], "bash /tests/test.sh")
        self.assertFalse(any("/solution" in argument for arguments in calls for argument in arguments))
        self.assertTrue(any(arguments[0] == "cp" and arguments[2].endswith(":/tests") for arguments in calls))

    def test_outer_timeout_kills_container_before_logs_and_always_removes_it(self):
        docker = FakeDocker(timed_out=True, start_code=-9, reward="1\n")
        result, docker, _ = self.run_oracle(docker)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["timed_out"])
        calls = [arguments for arguments, _ in docker.calls]
        start_index = next(index for index, args in enumerate(calls) if args[0] == "start")
        kill_index = next(index for index, args in enumerate(calls) if args[0] == "kill")
        download_index = next(index for index, args in enumerate(calls) if args[0] == "cp" and ":/logs/verifier/." in args[1])
        self.assertLess(start_index, kill_index)
        self.assertLess(kill_index, download_index)
        self.assertEqual(calls[-1][0], "rm")
        self.assertEqual(calls[kill_index][1], calls[start_index][-1])

    def test_internal_verifier_timeout_is_classified_as_timeout(self):
        result, docker, _ = self.run_oracle(FakeDocker(
            reward="0\n", report={"timed_out": True, "reward": 0, "execution_complete": False},
            start_code=124, timed_out=False))
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["timed_out"])
        self.assertFalse(any(arguments[0] == "kill" for arguments, _ in docker.calls))

    def test_load_failure_is_archived_as_infrastructure_error(self):
        result, docker, job = self.run_oracle(FakeDocker(load_error=FactoryError("cannot connect to the Docker daemon")))
        self.assertEqual(result["status"], "infrastructure_failed")
        self.assertIsNone(result["reward"])
        self.assertEqual([arguments[0] for arguments, _ in docker.calls], ["load", "rm"])
        self.assertTrue((job / "oracle_result.json").exists())

    def test_container_creation_failure_does_not_attempt_upload_or_start(self):
        result, docker, _ = self.run_oracle(FakeDocker(create_code=125))
        self.assertEqual(result["status"], "infrastructure_failed")
        self.assertEqual([arguments[0] for arguments, _ in docker.calls], ["load", "create", "rm"])

    def test_missing_boolean_nonfinite_and_malformed_rewards_are_rejected(self):
        for reward in (None, "true\n", "false\n", "True\n", "nan\n", "inf\n", "Infinity\n", "not-a-score\n"):
            with self.subTest(reward=reward):
                result, _, _ = self.run_oracle(FakeDocker(reward=reward))
                self.assertEqual(result["status"], "missing_reward")
                self.assertIsNone(result["reward"])

    def test_partial_reward_and_nonzero_exit_retain_baseline_score(self):
        for reward, code, status in (("0.75\n", 0, "failed"), ("1\n", 1, "passed"), ("0.999999\n", 0, "passed")):
            with self.subTest(reward=reward, code=code):
                result, _, _ = self.run_oracle(FakeDocker(reward=reward, start_code=code))
                self.assertEqual(result["status"], status)
                self.assertEqual(result["reward"], float(reward))

    def test_infrastructure_output_takes_precedence_over_reward(self):
        result, _, _ = self.run_oracle(FakeDocker(reward="1\n", stderr="no space left on device"))
        self.assertEqual(result["status"], "infrastructure_failed")

    def test_download_failure_is_visible_and_cannot_pass_without_reward(self):
        result, _, _ = self.run_oracle(FakeDocker(download_code=1))
        self.assertEqual(result["status"], "missing_reward")
        self.assertIn("container log path not found", result["log_download_error"])

    def test_download_transport_failure_is_infrastructure_error_even_after_timeout(self):
        for timed_out in (False, True):
            with self.subTest(timed_out=timed_out):
                result, docker, job = self.run_oracle(FakeDocker(
                    timed_out=timed_out, download_code=1,
                    download_stderr="Error response from daemon: connection reset by peer"))
                self.assertEqual(result["status"], "infrastructure_failed")
                self.assertIsNone(result["reward"])
                self.assertIn("connection reset", result["log_download_error"])
                self.assertEqual(docker.calls[-1][0][0:2], ["rm", "-f"])
                self.assertEqual(json.loads((job / "oracle_result.json").read_text())["status"], "infrastructure_failed")

    def test_cleanup_exception_preserves_run_result_and_evidence(self):
        result, _, job = self.run_oracle(FakeDocker(cleanup_error=FactoryError("cleanup transport unavailable")))
        self.assertEqual(result["status"], "passed")
        self.assertIn("cleanup transport unavailable", result["cleanup_error"])
        self.assertTrue((job / "oracle_result.json").exists())

    def test_nonzero_cleanup_is_recorded_without_losing_result(self):
        result, _, _ = self.run_oracle(FakeDocker(cleanup_code=1))
        self.assertEqual(result["status"], "passed")
        self.assertIn("cleanup", result["cleanup_error"])

    def test_non_object_report_is_archived_as_infrastructure_error(self):
        for report in (None, [], True, "invalid report"):
            with self.subTest(report=report):
                result, _, job = self.run_oracle(FakeDocker(report=report))
                self.assertEqual(result["status"], "infrastructure_failed")
                self.assertTrue((job / "oracle_result.json").exists())

    def test_invalid_mode_has_no_container_side_effects(self):
        docker = FakeDocker()
        with self.assertRaises(ValueError):
            DockerOracle(docker).run(self.instance, self.archive, self.root / "job-invalid", mode="student")
        self.assertEqual(docker.calls, [])
        self.assertFalse((self.root / "job-invalid").exists())


if __name__ == "__main__":
    unittest.main()
