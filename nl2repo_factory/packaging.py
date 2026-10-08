"""Build NL2Repo task directories without running Docker or repository code."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import shutil
import tempfile

from . import verifier


INSTRUCTION = (
    "According to the start.md in the workspace, implement the entire project as per the requirements specified in the document, ensuring that the final product can be directly run in the current directory. The running requirements should comply with the <API Usage Guide> section of the document.\n"
    "Note that all required dependencies have already been pre-configured in the local environment. You are strictly prohibited from fetching external information or dependencies. Do not use commands such as git clone, pip install, curl, wget, or similar tools. Please complete this task step by step.\n"
)
PACKAGE_DEFAULTS = ("pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "pytest.ini", "requirements.txt", "requirements-dev.txt")
SNAPSHOT_EXCLUDES = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SOURCE_ROOTS = ("/workspace", "/testbed", "/repo", "/app", "/src")


def is_test_file(path: str) -> bool:
    parts = Path(path).parts
    name = parts[-1]
    return (any(part in {"tests", "test"} for part in parts[:-1])
            or name.startswith("test_") or name.endswith("_test.py") or name == "tests.py")


def build_environment_dockerfile(base_image: str) -> str:
    if not isinstance(base_image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]*", base_image):
        raise ValueError("invalid Docker base image reference")
    cleanup = '''from pathlib import Path
import os
import shutil
import sys
if sys.version_info < (3, 11):
    raise RuntimeError("NL2Repo Docker backend requires Python 3.11 or newer")
if not (os.path.isfile("/bin/bash") and os.access("/bin/bash", os.X_OK)):
    raise RuntimeError("NL2Repo Docker backend requires executable /bin/bash")
roots = ("/workspace", "/testbed", "/repo", "/app", "/src")
def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
for root in roots:
    remove(Path(root))
Path("/workspace").mkdir(parents=True)
Path("/logs").mkdir(parents=True, exist_ok=True)
library = Path("/usr/local/lib")
if library.exists():
    artifacts = [p for p in library.rglob("*") if p.name.endswith(".egg-link") or p.name.startswith("__editable__")]
    for path in sorted(artifacts, key=lambda p: len(p.parts), reverse=True):
        remove(path)
    for path in library.rglob("*.pth"):
        if "site-packages" in path.parts and any(root in path.read_text(errors="replace") for root in roots):
            remove(path)
'''
    code = "exec(" + repr(cleanup) + ")"
    return (f"FROM {base_image}\nUSER root\nRUN python3 -c {shlex.quote(code)}\n"
            "COPY start.md /workspace/start.md\nWORKDIR /workspace\n")


def _existing(repo: Path, values: list, *, files_only: bool = False) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("asset declarations must be arrays")
    found = []
    for value in values:
        relative = verifier.relative_asset_path(value)
        source = repo / relative
        if source.is_symlink() and not source.resolve().is_relative_to(repo):
            raise ValueError(f"asset symlink escapes repository: {relative}")
        if not source.exists():
            continue
        source = verifier.checked_source(repo, relative)
        if not files_only or source.is_file():
            found.append(relative)
    return sorted(set(found))


def _copy_tree(source: Path, destination: Path, repo: Path, *, snapshot: bool = False,
               ancestors: frozenset[Path] = frozenset()) -> None:
    resolved = source.resolve(strict=True)
    if not resolved.is_relative_to(repo):
        raise ValueError(f"source symlink escapes repository: {source}")
    if resolved in ancestors:
        raise ValueError(f"cyclic source symlink: {source}")
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir()):
            if snapshot and (child.name in SNAPSHOT_EXCLUDES or child.name.endswith(".pyc")):
                continue
            _copy_tree(child, destination / child.name, repo, snapshot=snapshot,
                       ancestors=ancestors | {resolved})
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.is_dir():
            raise ValueError(f"conflicting asset path: {destination}")
        shutil.copy2(source, destination)
    else:
        raise ValueError(f"source is not a regular file or directory: {source}")


def _normalize_commands(commands: list[str], repo: Path, roots: list[str],
                        repo_alias: Path | None = None) -> list[str]:
    if not isinstance(commands, list) or not commands:
        raise ValueError("test_commands must be a nonempty list")
    normalized = []
    known_roots = {str(repo), "/workspace/repo", "/tmp/build/repo", "/opt/repo"}
    if repo_alias is not None:
        known_roots.add(str(repo_alias))
    old_roots = sorted(known_roots, key=len, reverse=True)
    for command in commands:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("test command must be a nonempty string")
        for source in old_roots:
            command = re.sub(r"(?<![A-Za-z0-9_./-])" + re.escape(source) + r"(?=/|\s|$)", "/workspace", command)
        if len(roots) == 1 and roots[0] not in {"test", "tests"}:
            command = re.sub(r"(?<!\S)(?:\./)?tests(?=\s|$)", lambda _: shlex.quote(roots[0]), command)
        normalized.append(command)
    return normalized


def _task_toml(project_name: str, image_archive: Path) -> str:
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    return f'''version = "1.0"

[metadata]
author_name = "TeamFactory"
author_email = "unknown"
difficulty = "medium"
category = "code-generation"
tags = ["code-generation", "0-to-1", "teamfactory", "python", {quote(project_name)}]

[verifier]
timeout_sec = 1200.0

[agent]
timeout_sec = 3600.0

[environment]
build_timeout_sec = 1800.0
cpus = 2
memory_mb = 8192
storage_mb = 20480
docker_image_archive = {quote(image_archive)}
'''


def materialize_instance(
    repo: Path, destination: Path, start_md: Path, manifest: Path, a1: dict,
    ast_payload: dict, image_archive: Path, project_name: str,
) -> dict:
    """Create a new task bundle. Existing destinations are never overwritten.

    No image is built here: ``image_archive`` is the future runner-accessible
    archive path. The Docker build context is the generated environment folder.
    """
    repo_alias = Path(repo).absolute()
    repo = Path(repo).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"destination already exists: {destination}")
    if destination.resolve().is_relative_to(repo):
        raise ValueError("destination must be outside the reference repository")
    if not isinstance(project_name, str) or not project_name.strip():
        raise ValueError("project_name must be a nonempty string")
    env_spec = a1["env_spec"]
    package_files = _existing(repo, env_spec.get("package_files", []) + list(PACKAGE_DEFAULTS), files_only=True)
    declared_tests = env_spec.get("test_files", [])
    if not isinstance(declared_tests, list):
        raise ValueError("test_files must be an array")
    python_files = ast_payload.get("python_files", [])
    if not isinstance(python_files, list):
        raise ValueError("AST python_files must be an array")
    # Accept path records as well as the scanner's usual array of path strings.
    ast_paths = [item.get("path") if isinstance(item, dict) else item for item in python_files]
    ast_paths = [verifier.relative_asset_path(path) for path in ast_paths]
    test_files = _existing(repo, declared_tests + [path for path in ast_paths if is_test_file(path)], files_only=True)
    if not test_files:
        test_files = _existing(repo, [path.relative_to(repo).as_posix() for path in repo.rglob("*.py")
                                      if is_test_file(path.relative_to(repo).as_posix())], files_only=True)
    if not test_files:
        raise ValueError("no existing test files were selected")
    fixtures = _existing(repo, env_spec.get("fixture_files", []))
    roots = sorted({verifier.test_root_for(path) for path in test_files})
    collected = a1.get("oracle_report", {}).get("collected")
    total = collected if isinstance(collected, int) and not isinstance(collected, bool) and collected > 0 else ast_payload.get("test_case_count", 0)
    verifier.score_output("", total, set())
    config = {
        "pro_name": project_name, "test_case_count": total,
        "test_commands": _normalize_commands(env_spec["test_commands"], repo, roots, repo_alias),
        "test_files": test_files, "package_files": package_files, "fixture_files": fixtures,
    }
    dockerfile = build_environment_dockerfile(a1["docker"]["image"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-pack-", dir=destination.parent))
    owned_destination = False
    try:
        environment = staging / "environment"
        environment.mkdir()
        shutil.copyfile(start_md, environment / "start.md")
        shutil.copyfile(manifest, environment / "api_manifest.json")
        (environment / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        (staging / "instruction.md").write_text(INSTRUCTION, encoding="utf-8")
        (staging / "task.toml").write_text(_task_toml(project_name, Path(image_archive)), encoding="utf-8")
        _copy_tree(repo, staging / "solution/oracle", repo, snapshot=True)
        solve = staging / "solution/solve.sh"
        solve.write_text('#!/bin/bash\nset -euo pipefail\ncp -a /solution/oracle/. /workspace/\n', encoding="utf-8")
        solve.chmod(0o755)
        tests_dir = staging / "tests"
        reference = tests_dir / "reference"
        reference.mkdir(parents=True)
        assets = list(dict.fromkeys(roots + test_files + package_files + fixtures))
        for relative in assets:
            _copy_tree(verifier.checked_source(repo, relative), reference / relative, repo)
        (tests_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        shutil.copyfile(Path(verifier.__file__), tests_dir / "verifier.py")
        runner = tests_dir / "test.sh"
        runner.write_text('#!/bin/bash\nset -euo pipefail\nexec python3 /tests/verifier.py "$@"\n', encoding="utf-8")
        runner.chmod(0o755)
        reference_files = sorted(path.relative_to(reference).as_posix() for path in reference.rglob("*") if path.is_file())
        implementation_paths = set(ast_payload.get("implementation_python_files", []))
        warnings = []
        for path in reference_files:
            if path.endswith(".py") and (path in implementation_paths or
                    (not is_test_file(path) and Path(path).name != "conftest.py" and path not in package_files)):
                warnings.append({"kind": "implementation_in_reference", "path": path,
                                 "reason": "baseline asset selection may restore production implementation"})
        whitelist = sorted(set(test_files) | {
            path for path in reference_files if path.endswith(".py")
            and any(path == root or path.startswith(root + "/") for root in roots)
        })
        # Exclusive mkdir prevents replacing an existing directory, even if one
        # appears while staging. Only this invocation's files are ever cleaned.
        destination.mkdir()
        owned_destination = True
        for child in staging.iterdir():
            child.rename(destination / child.name)
        return {
            "destination": str(destination), "config": config, "test_roots": roots,
            "whitelist": whitelist, "reference_files": reference_files,
            "asset_warnings": warnings, "image_archive": str(image_archive),
            "docker_build_context": str(destination / "environment"),
            "image_built": False,
        }
    except BaseException:
        if owned_destination:
            shutil.rmtree(destination)
        raise
    finally:
        shutil.rmtree(staging)
