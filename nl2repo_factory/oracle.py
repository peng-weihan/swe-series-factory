"""Run the exported verifier in a fresh, network-disabled Docker container."""
from __future__ import annotations

import json
import shutil
import tomllib
import uuid
from pathlib import Path

from .common import FactoryError, require_process, write_json
from .contracts import extract_reward, is_infrastructure_error
from .docker import DockerAdapter, image_name


class DockerOracle:
    def __init__(self, docker: DockerAdapter, timeout: float = 3600):
        self.docker, self.timeout = docker, timeout

    def run(self, instance: Path, archive: Path, job_dir: Path, *, mode: str = "oracle") -> dict:
        if mode not in {"oracle", "empty"}:
            raise ValueError("mode must be oracle or empty")
        job_dir.mkdir(parents=True, exist_ok=False)
        trial, copied = job_dir / "trial", job_dir / "instance"
        shutil.copytree(instance, copied, symlinks=True)
        workspace, logs = trial / "workspace", trial / "verifier"
        workspace.mkdir(parents=True)
        logs.mkdir()
        shutil.copy2(copied / "environment/start.md", workspace / "start.md")
        task = tomllib.loads((copied / "task.toml").read_text())
        environment = task.get("environment", {})
        tag = image_name(instance.name)
        # The tag is recorded by the controller because staging paths differ from task IDs.
        metadata = copied / "build_metadata.json"
        if metadata.exists():
            tag = json.loads(metadata.read_text())["image_tag"]
        name = "nl2repo-oracle-" + uuid.uuid4().hex
        result = {"reward": None, "trial_dir": str(trial), "mode": mode}
        try:
            self.docker.load(archive)
            command = "bash /solution/solve.sh && bash /tests/test.sh" if mode == "oracle" else "bash /tests/test.sh"
            arguments = ["create", "--name", name, "--network", "none",
                         "--cpus", str(environment.get("cpus", 2)), "--pids-limit", "512",
                         "--memory", str(environment.get("memory_mb", 8192)) + "m",
                         "--entrypoint", "/bin/bash", "--workdir", "/workspace"]
            arguments.extend([tag, "-c", command])
            require_process(self.docker.execute(arguments), "Oracle container creation")
            # docker cp works across remote daemons; host bind paths would not.
            require_process(self.docker.execute(["cp", str(workspace) + "/.", name + ":/workspace"]), "Workspace upload")
            require_process(self.docker.execute(["cp", str(copied / "tests"), name + ":/tests"]), "Private tests upload")
            if mode == "oracle":
                require_process(self.docker.execute(["cp", str(copied / "solution"), name + ":/solution"]), "Oracle upload")
            execution = self.docker.execute(["start", "--attach", name], timeout=self.timeout)
            if execution.timed_out:
                # Kill the container, not just the Docker client, before retrieving incomplete logs.
                self.docker.execute(["kill", name], timeout=60)
            download = self.docker.execute(["cp", name + ":/logs/verifier/.", str(logs)])
            if download.returncode:
                result["log_download_error"] = download.stderr
            (logs / "stdout.txt").write_text(execution.stdout)
            (logs / "stderr.txt").write_text(execution.stderr)
            result.update({"returncode": execution.returncode, "timed_out": execution.timed_out,
                           "stdout": execution.stdout, "stderr": execution.stderr})
            report_path = logs / "report.json"
            if report_path.is_file():
                result["report"] = json.loads(report_path.read_text())
                if not isinstance(result["report"], dict):
                    raise FactoryError("Verifier report.json must be a JSON object")
                result["timed_out"] = execution.timed_out or result["report"].get("timed_out") is True
            reward_path = logs / "reward.txt"
            if reward_path.is_file():
                try:
                    result["reward"] = extract_reward(float(reward_path.read_text().strip()))
                except ValueError:
                    pass
            diagnostics = "\n".join((execution.stdout, execution.stderr,
                                      download.stdout, download.stderr))
            if is_infrastructure_error(diagnostics):
                result["status"] = "infrastructure_failed"
            elif result["timed_out"]:
                result["status"] = "timeout"
            elif result["reward"] is None:
                result["status"] = "missing_reward"
            else:
                result["status"] = "passed" if result["reward"] >= 0.999999 else "failed"
        except (FactoryError, OSError, ValueError) as exc:
            result.update(status="infrastructure_failed", error=str(exc), stderr=str(exc))
        finally:
            try:
                require_process(self.docker.execute(["rm", "-f", name], timeout=60), "Oracle container cleanup")
            except FactoryError as exc:
                result["cleanup_error"] = str(exc)
        write_json(trial / "result.json", {"verifier_result": {"rewards": {"reward": result["reward"]}},
                                           "status": result["status"], "mode": mode})
        write_json(job_dir / "oracle_result.json", result)
        return result
