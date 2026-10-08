"""Fresh host or offline Docker execution with durable native reports."""
from __future__ import annotations

import os
import math
import shutil
import sys
import uuid
from pathlib import Path

from nl2repo_factory.common import FactoryError, require_process, run_process, write_json
from nl2repo_factory.docker import DockerAdapter
from .reports import parse_junit
from .snapshots import copy_tree


def command(value: list[str], *, empty=False) -> list[str]:
    if empty and value == []:
        return value
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v and "\x00" not in v for v in value):
        raise FactoryError("Commands must be nonempty argv arrays")
    if Path(value[0]).name in {"sh", "bash", "zsh", "fish"} and "-c" in value:
        raise FactoryError("Test/build argv may not use shell -c; use a recorded script")
    return value


class Runner:
    def __init__(self, *, backend="docker", image=None, python=None, timeout=300,
                 docker_binary="docker", docker_host=None, memory="2g", cpus="2"):
        if backend not in {"host", "docker"}:
            raise FactoryError("backend must be host or docker")
        if backend == "docker" and not image:
            raise FactoryError("Docker execution requires an image")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise FactoryError("Runtime timeout must be positive and finite")
        if python is not None and (not isinstance(python, str) or not python):
            raise FactoryError("Runtime python must be an executable name/path")
        if image is not None and (not isinstance(image, str) or any(c.isspace() for c in image)):
            raise FactoryError("Runtime image must be a single image reference")
        if not isinstance(memory, str) or not memory or not isinstance(cpus, (str, int, float)):
            raise FactoryError("Invalid container resource limits")
        self.backend, self.image = backend, image
        self.python = python or (sys.executable if backend == "host" else "python3")
        self.timeout, self.memory, self.cpus = timeout, memory, str(cpus)
        self.docker = DockerAdapter(docker_binary, docker_host, timeout)

    def run(self, source: Path, argv: list[str], evidence: Path) -> dict:
        command(argv)
        evidence.mkdir(parents=True, exist_ok=False)
        workspace = copy_tree(source, evidence / "workspace")
        env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
               "PYTEST_ADDOPTS": "", "TZ": "UTC", "LC_ALL": "C"}
        actual = [self.python if x == "{python}" else x for x in argv]
        if self.backend == "host":
            execution = run_process(actual, cwd=workspace, env=dict(os.environ, **env), timeout=self.timeout)
            identity = {"backend": "host", "isolated": False, "image": None}
        else:
            identity = {"backend": "docker", "isolated": True, "image": self.docker.inspect(self.image)}
            name = "swe-factory-" + uuid.uuid4().hex
            args = ["create", "--name", name, "--user", "0:0", "--network", "none", "--memory", self.memory,
                    "--cpus", self.cpus, "--pids-limit", "256", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--workdir", "/factory-workspace",
                    "--entrypoint", "/bin/sleep"]
            for key, value in env.items():
                args += ["--env", f"{key}={value}"]
            require_process(self.docker.execute([*args, self.image, "infinity"]), "Container create")
            try:
                require_process(self.docker.execute(["start", name]), "Container start")
                require_process(self.docker.execute(["exec", name, self.python, "-c",
                    "from pathlib import Path; p=Path('/factory-workspace'); p.mkdir(exist_ok=True); assert not any(p.iterdir()), 'image has a nonempty execution workspace'"]), "Check fresh workspace")
                require_process(self.docker.execute(["cp", str(workspace) + "/.", name + ":/factory-workspace"]), "Copy inputs")
                execution = self.docker.execute(["exec", name, *actual], timeout=self.timeout)
                if execution.timed_out:
                    self.docker.execute(["stop", "--time", "0", name], timeout=30)
                # Copy into a NEW directory, never overlay stale host files.
                collected = evidence / "collected"
                collected.mkdir()
                require_process(self.docker.execute(["cp", name + ":/factory-workspace/.", str(collected)]), "Collect outputs")
                workspace = collected
            finally:
                self.docker.execute(["rm", "-f", name], timeout=30)
        write_json(evidence / "execution.json", dict(execution.to_dict(), runtime=identity))
        (evidence / "stdout.txt").write_text(execution.stdout)
        (evidence / "stderr.txt").write_text(execution.stderr)
        return {"workspace": workspace, "execution": execution, "runtime": identity}

    def suite(self, source: Path, argv: list[str], evidence: Path) -> dict:
        argv = command(argv).copy()
        if any("{report}" in arg for arg in argv):
            argv = [arg.replace("{report}", ".factory-report.xml") for arg in argv]
        elif "pytest" in argv or Path(argv[0]).name == "pytest":
            if any(arg.startswith("--junit") for arg in argv):
                raise FactoryError("Use {report} for explicit JUnit paths")
            argv += ["--junitxml=.factory-report.xml", "--rootdir=.", "-o", "junit_family=xunit1", "-p", "no:cacheprovider"]
        else:
            raise FactoryError("Non-pytest runners must emit JUnit using a {report} argument")
        result = self.run(source, argv, evidence)
        report_path = result["workspace"] / ".factory-report.xml"
        report = parse_junit(report_path)
        execution = result["execution"]
        if report_path.is_file():
            shutil.copy2(report_path, evidence / "results.xml")
        has_failure = any(s in {"failed", "error"} for s in report["tests"].values())
        if execution.timed_out or execution.returncode not in (0, 1) or (execution.returncode == 0 and has_failure):
            report["healthy"] = False
            report["errors"].append("Timeout or invalid runner exit status")
        if execution.returncode == 1 and not has_failure:
            report["healthy"] = False
            report["errors"].append("Runner failed without a reported test failure")
        report.update(returncode=execution.returncode, timed_out=execution.timed_out,
                      evidence=str(evidence.resolve()))
        write_json(evidence / "report.json", report)
        return report
