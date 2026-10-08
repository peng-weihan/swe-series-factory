import ast
import json
from pathlib import Path
import shlex
import tempfile
import tomllib
import unittest

from nl2repo_factory.packaging import INSTRUCTION, build_environment_dockerfile, materialize_instance


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "tests/test_api.py").write_text("def test_api(): pass\n")
        (self.repo / "tests/conftest.py").write_text("# fixture\n")
        (self.repo / "tagkit").mkdir()
        (self.repo / "tagkit/api.py").write_text("def normalize_tags(x): return x\n")
        (self.repo / "pyproject.toml").write_text('[project]\nname="tagkit"\nversion="0.1"\n')
        (self.repo / ".git").mkdir()
        (self.repo / ".git/config").write_text("private history")
        (self.repo / "__pycache__").mkdir()
        (self.repo / "__pycache__/api.pyc").write_bytes(b"compiled")
        self.start = self.root / "start.md"
        self.start.write_text("# TagKit\n")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text('{"project": "TagKit"}\n')
        self.a1 = {"docker": {"image": "tagkit-base:latest"}, "oracle_report": {"collected": 4},
                   "env_spec": {"package_files": [], "test_files": ["tests/test_api.py"],
                                "fixture_files": [], "test_commands": ["python -m pytest -q"]}}
        self.ast = {"python_files": ["tagkit/api.py", "tests/test_api.py"],
                    "implementation_python_files": ["tagkit/api.py"], "test_case_count": 1}
        self.destination = self.root / "task"

    def materialize(self):
        return materialize_instance(self.repo, self.destination, self.start, self.manifest,
                                    self.a1, self.ast, self.root / "images/task.tar", "TagKit")

    def test_complete_bundle_contract_and_snapshot_exclusions(self):
        result = self.materialize()
        self.assertEqual(result["config"]["test_case_count"], 4)
        self.assertEqual(result["config"]["package_files"], ["pyproject.toml"])
        self.assertEqual(result["test_roots"], ["tests"])
        self.assertEqual(result["asset_warnings"], [])
        self.assertIn("tests/conftest.py", result["whitelist"])
        self.assertEqual((self.destination / "instruction.md").read_text(), INSTRUCTION)
        self.assertEqual((self.destination / "environment/start.md").read_bytes(), self.start.read_bytes())
        self.assertEqual((self.destination / "environment/api_manifest.json").read_bytes(), self.manifest.read_bytes())
        self.assertTrue((self.destination / "solution/oracle/tagkit/api.py").exists())
        self.assertFalse((self.destination / "solution/oracle/.git").exists())
        self.assertFalse((self.destination / "solution/oracle/__pycache__").exists())
        self.assertFalse((self.destination / "tests/reference/tagkit").exists())
        self.assertIn('cp -a /solution/oracle/. /workspace/', (self.destination / "solution/solve.sh").read_text())
        self.assertTrue((self.destination / "tests/test.sh").stat().st_mode & 0o111)
        self.assertTrue((self.destination / "tests/verifier.py").exists())
        metadata = tomllib.loads((self.destination / "task.toml").read_text())
        self.assertEqual(metadata["environment"]["cpus"], 2)
        self.assertEqual(metadata["environment"]["docker_image_archive"], str(self.root / "images/task.tar"))
        self.assertEqual(metadata["metadata"]["difficulty"], "medium")
        self.assertFalse(result["image_built"])

    def test_existing_destination_is_never_modified(self):
        self.destination.mkdir()
        (self.destination / "KEEP").write_text("valid previous task")
        with self.assertRaises(FileExistsError):
            self.materialize()
        self.assertEqual((self.destination / "KEEP").read_text(), "valid previous task")

    def test_inside_repo_destination_rejected(self):
        self.destination = self.repo / "new-task"
        with self.assertRaises(ValueError):
            self.materialize()
        self.assertFalse(self.destination.exists())

    def test_collected_fallback_and_missing_assets(self):
        self.a1["oracle_report"]["collected"] = True
        self.a1["env_spec"]["fixture_files"] = ["missing-file.txt"]
        result = self.materialize()
        self.assertEqual(result["config"]["test_case_count"], 1)
        self.assertEqual(result["config"]["fixture_files"], [])

    def test_baseline_root_leak_is_explicitly_reported(self):
        (self.repo / "tagkit/api_test.py").write_text("def test_api(): pass\n")
        self.a1["env_spec"]["test_files"] = ["tagkit/api_test.py"]
        self.ast["python_files"] = ["tagkit/api.py", "tagkit/api_test.py"]
        result = self.materialize()
        self.assertEqual(result["test_roots"], ["tagkit"])
        self.assertTrue((self.destination / "tests/reference/tagkit/api.py").exists())
        self.assertIn("tagkit/api.py", [warning["path"] for warning in result["asset_warnings"]])

    def test_fixture_and_package_selection_do_not_autodiscover_unlisted_data(self):
        (self.repo / "fixtures").mkdir()
        (self.repo / "fixtures/selected.txt").write_text("included")
        (self.repo / "fixtures/unlisted.txt").write_text("not included")
        self.a1["env_spec"]["fixture_files"] = ["fixtures/selected.txt"]
        self.materialize()
        self.assertTrue((self.destination / "tests/reference/fixtures/selected.txt").exists())
        self.assertFalse((self.destination / "tests/reference/fixtures/unlisted.txt").exists())

    def test_command_source_boundary_and_single_root_rewriting(self):
        (self.repo / "tagkit/api_test.py").write_text("def test_api(): pass\n")
        self.a1["env_spec"]["test_files"] = ["tagkit/api_test.py"]
        self.ast["python_files"] = ["tagkit/api_test.py"]
        self.a1["env_spec"]["test_commands"] = [
            f"cd {self.repo} && python -m pytest tests",
            "cd /tmp/build/repo && python -m pytest ./tests",
            "echo /workspace/repository /opt/repository",
        ]
        commands = self.materialize()["config"]["test_commands"]
        self.assertEqual(commands[0], "cd /workspace && python -m pytest tagkit")
        self.assertEqual(commands[1], "cd /workspace && python -m pytest tagkit")
        self.assertEqual(commands[2], "echo /workspace/repository /opt/repository")

    def test_unsafe_declared_paths_reject_without_partial_bundle(self):
        self.a1["env_spec"]["fixture_files"] = ["../start.md"]
        with self.assertRaises(ValueError):
            self.materialize()
        self.assertFalse(self.destination.exists())

    def test_external_symlink_in_snapshot_rejects_without_partial_bundle(self):
        (self.repo / "escape").symlink_to(self.start)
        with self.assertRaises(ValueError):
            self.materialize()
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.glob(".task-pack-*")), [])

    def test_internal_file_symlink_is_copied_as_regular_file(self):
        (self.repo / "alias.py").symlink_to("tagkit/api.py")
        self.materialize()
        alias = self.destination / "solution/oracle/alias.py"
        self.assertFalse(alias.is_symlink())
        self.assertEqual(alias.read_text(), (self.repo / "tagkit/api.py").read_text())

    def test_ast_empty_test_list_falls_back_to_filesystem(self):
        self.a1["env_spec"]["test_files"] = []
        self.ast["python_files"] = []
        self.assertEqual(self.materialize()["config"]["test_files"], ["tests/conftest.py", "tests/test_api.py"])


class DockerfileTests(unittest.TestCase):
    def test_runtime_requirements_are_checked_before_any_source_cleanup(self):
        dockerfile = build_environment_dockerfile("python:3.11-slim")
        run = next(line.removeprefix("RUN ") for line in dockerfile.splitlines() if line.startswith("RUN "))
        arguments = shlex.split(run)
        self.assertEqual(arguments[:2], ["python3", "-c"])
        wrapper = ast.parse(arguments[2])
        script = ast.literal_eval(wrapper.body[0].value.args[0])
        compile(script, "<generated-docker-cleanup>", "exec")
        checks = script.split("roots =", 1)[0]
        self.assertIn("import sys", checks)
        self.assertIn("import os", checks)
        self.assertIn("sys.version_info < (3, 11)", checks)
        self.assertIn('os.path.isfile("/bin/bash")', checks)
        self.assertIn('os.access("/bin/bash", os.X_OK)', checks)
        self.assertIn("requires Python 3.11 or newer", checks)
        self.assertIn("requires executable /bin/bash", checks)

    def test_cleanup_is_container_only_and_preserves_dependencies(self):
        dockerfile = build_environment_dockerfile("python:3.11-slim@sha256:abc")
        self.assertTrue(dockerfile.startswith("FROM python:3.11-slim@sha256:abc\nUSER root\n"))
        self.assertIn(".egg-link", dockerfile)
        self.assertIn("RUN python3 -c ", dockerfile)
        self.assertIn("__editable__", dockerfile)
        self.assertIn("*.pth", dockerfile)
        self.assertIn("site-packages", dockerfile)
        self.assertIn("COPY start.md /workspace/start.md", dockerfile)
        self.assertIn("WORKDIR /workspace", dockerfile)
        self.assertNotIn("pip uninstall", dockerfile)
        self.assertEqual(len(dockerfile.splitlines()), 5)

    def test_base_image_cannot_inject_dockerfile_instructions(self):
        for image in ("python:3.11\nRUN evil", "--platform=x python", "", "$(evil)"):
            with self.subTest(image=image), self.assertRaises(ValueError):
                build_environment_dockerfile(image)


if __name__ == "__main__":
    unittest.main()
