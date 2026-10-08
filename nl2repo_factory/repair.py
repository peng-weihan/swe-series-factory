"""Restricted Oracle repair validation and recoverable filesystem transactions.

The caller supplies ``trusted_report`` from its own Oracle ledger, never from the
repair agent. Validation is a gate, not a substitute for running the Oracle again.
Dockerfile repairs deliberately support only appended, declared RUN layers. Image
archives are opaque files here; building and leak checking remain runner duties.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import tomllib
import uuid


FROZEN_FILES = ("environment/start.md", "environment/api_manifest.json", "instruction.md")
_BYPASS = re.compile(
    r"reward\.txt|/logs/verifier|\b(?:echo|printf)\b|\bexit\s+0\b|\|\|\s*(?:true\b|:)",
    re.IGNORECASE,
)
_IMAGE_FORBIDDEN = re.compile(
    r"\b(?:docker|podman|sudo|su|mount|umount|chroot|nsenter|unshare)\b"
    r"|--(?:privileged|mount|volume|device)\b"
    r"|/(?:solution|tests)(?:[/\s'\";]|$)|\b(?:solution|tests)/",
    re.IGNORECASE,
)


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"invalid relative path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or value == ".":
        raise ValueError(f"invalid relative path: {value!r}")
    return value


def _snapshot(root: Path, *, check_links: bool = True) -> dict[str, tuple]:
    """Hash regular files and link targets without following directory symlinks."""
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(path.lstat().st_mode)
            if path.is_symlink():
                if check_links and not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError(f"symlink escapes instance: {relative}")
                result[relative] = ("symlink", os.readlink(path), mode)
            elif path.is_file():
                result[relative] = ("file", _file_hash(path), mode)
            elif not path.is_dir():
                raise ValueError(f"unsupported filesystem entry: {relative}")
    return result


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_hash(path: Path) -> str:
    # Backups can contain absolute links into the original canonical path. Their
    # link text, not its temporarily relocated resolution, establishes identity.
    snapshot = _snapshot(path, check_links=False)
    directories = {".": stat.S_IMODE(path.stat().st_mode)}
    for parent, names, _ in os.walk(path, followlinks=False):
        for name in names:
            directory = Path(parent) / name
            if not directory.is_symlink():
                directories[directory.relative_to(path).as_posix()] = stat.S_IMODE(directory.stat().st_mode)
    return hashlib.sha256(json.dumps([snapshot, directories], sort_keys=True).encode()).hexdigest()


def _fsync_directory(path: Path) -> None:
    """Persist directory entries after renames on supported POSIX filesystems."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_replace(source: Path, destination: Path) -> None:
    os.replace(source, destination)
    _fsync_directory(destination.parent)
    if source.parent != destination.parent:
        _fsync_directory(source.parent)


def _flush_tree(root: Path) -> None:
    for parent, _, files in os.walk(root, topdown=False, followlinks=False):
        for name in files:
            path = Path(parent) / name
            if not path.is_symlink():
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
        _fsync_directory(Path(parent))


def _string_list(value: object, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{label} must be a list of nonempty strings")
        return []
    return value


def _test_paths(root: Path, config: dict) -> set[str]:
    result = set()
    for name in config.get("test_files", []):
        result.add("tests/reference/" + _relative(name))
    reference = root / "tests/reference"
    if reference.is_dir():
        for path in reference.rglob("*"):
            if path.is_file() and (
                path.name.startswith("test") or path.name.endswith("_test.py") or path.name == "conftest.py"
            ):
                result.add(path.relative_to(root).as_posix())
    return result


def _constant_truth(node: ast.AST) -> bool:
    try:
        return bool(ast.literal_eval(node))
    except (ValueError, TypeError, SyntaxError):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
            try:
                left, right = ast.literal_eval(node.left), ast.literal_eval(node.comparators[0])
                if isinstance(node.ops[0], ast.Eq):
                    return left == right
                if isinstance(node.ops[0], ast.NotEq):
                    return left != right
            except (ValueError, TypeError, SyntaxError):
                pass
        return False


def _test_metrics(root: Path, paths: set[str]) -> tuple[int, int, int, int]:
    functions = assertions = skips = unconditional = 0
    for relative in sorted(paths):
        path = root / relative
        if path.suffix != ".py" or not path.is_file():
            continue
        tree = ast.parse(path.read_bytes(), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                functions += 1
            if isinstance(node, ast.Assert):
                assertions += 1
                unconditional += _constant_truth(node.test)
            if isinstance(node, (ast.Name, ast.Attribute)):
                name = node.id if isinstance(node, ast.Name) else node.attr
                if name in {"skip", "skipif", "skipIf", "skipUnless", "xfail", "expectedFailure"}:
                    skips += 1
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                name = node.func.attr
                if name.startswith("assert"):
                    assertions += 1
                if name == "assertTrue" and node.args and _constant_truth(node.args[0]):
                    unconditional += 1
                if name in {"assertEqual", "assertIs"} and len(node.args) >= 2:
                    try:
                        unconditional += ast.literal_eval(node.args[0]) == ast.literal_eval(node.args[1])
                    except (ValueError, TypeError, SyntaxError):
                        pass
    return functions, assertions, skips, unconditional


def _subsequence(old: list[str], new: list[str]) -> bool:
    remaining = iter(new)
    return all(any(token == candidate for candidate in remaining) for token in old)


def _docker_instructions(text: str) -> list[tuple[str, str]]:
    lines = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending += line[:-1].rstrip() + " " if line.endswith("\\") else line
        if line.endswith("\\"):
            continue
        parts = pending.split(None, 1)
        if len(parts) != 2:
            raise ValueError("malformed Dockerfile instruction")
        lines.append((parts[0].upper(), parts[1]))
        pending = ""
    if pending:
        raise ValueError("unterminated Dockerfile continuation")
    return lines


def validate_repair(
    canonical: Path, candidate: Path, response: dict, trusted_report: dict | None = None
) -> dict:
    """Return ``{passed, errors, changed_files}`` without changing either tree.

    Changes include additions, deletions, bytes, permissions, and symlink targets.
    All original identified test sources and the three task statements are frozen.
    The path contract is POSIX relative paths, including ``tests/reference/...``.
    """
    errors: list[str] = []
    changed: list[str] = []
    canonical, candidate = Path(canonical).resolve(), Path(candidate).resolve()
    try:
        if not canonical.is_dir() or not candidate.is_dir():
            raise ValueError("canonical and candidate must be existing directories")
        if canonical.is_relative_to(candidate) or candidate.is_relative_to(canonical):
            raise ValueError("canonical and candidate must be distinct, disjoint directories")
        before, after = _snapshot(canonical), _snapshot(candidate)
        changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    except (OSError, ValueError, RuntimeError) as exc:
        return {"passed": False, "errors": [str(exc)], "changed_files": changed}

    if not isinstance(response, dict):
        return {"passed": False, "errors": ["repair response must be an object"], "changed_files": changed}
    if response.get("schema_version") != "teamfactory.oracle_repair.v1":
        errors.append("unsupported repair schema_version")
    if response.get("status") not in ("repaired", "unrepairable"):
        errors.append("status must be repaired or unrepairable")
    if response.get("responsible_stage") not in ("agent1", "stage2_ast", "agent2_stage3"):
        errors.append("responsible_stage must explicitly identify an allowed stage")
    for field in ("root_cause", "diagnosis"):
        if not isinstance(response.get(field), str) or not response[field].strip():
            errors.append(f"{field} must be nonempty text")
    for field in ("evidence", "validation_notes"):
        _string_list(response.get(field), field, errors)
    declared = _string_list(response.get("changed_files"), "changed_files", errors)
    commands = _string_list(response.get("image_commands"), "image_commands", errors)
    for name in declared:
        try:
            _relative(name)
        except ValueError as exc:
            errors.append(str(exc))
    if len(declared) != len(set(declared)) or sorted(declared) != changed:
        errors.append("changed_files must exactly match actual file changes without duplicates")
    if response.get("status") == "unrepairable":
        if declared or commands or changed:
            errors.append("unrepairable requires no declared or actual changes and no image_commands")
        elif _tree_hash(canonical) != _tree_hash(candidate):
            errors.append("unrepairable requires no directory or directory permission changes")
        return {"passed": not errors, "errors": errors, "changed_files": changed}
    if response.get("status") == "repaired" and not declared:
        errors.append("repaired requires nonempty changed_files")

    for name in FROZEN_FILES:
        try:
            if name not in before or before.get(name) != after.get(name):
                errors.append(f"frozen task file changed or missing: {name}")
            elif (canonical / name).read_bytes() != (candidate / name).read_bytes():
                errors.append(f"frozen task file target changed: {name}")
        except OSError as exc:
            errors.append(f"cannot read frozen task file {name}: {exc}")
    try:
        old_config = json.loads((canonical / "tests/config.json").read_text())
        new_config = json.loads((candidate / "tests/config.json").read_text())
        if not isinstance(old_config, dict) or not isinstance(new_config, dict):
            raise ValueError("tests/config.json must be an object")
        old_tests = _string_list(old_config.get("test_files"), "original test_files", errors)
        new_tests = _string_list(new_config.get("test_files"), "candidate test_files", errors)
        if not set(old_tests).issubset(new_tests):
            errors.append("test_files cannot remove an original test")
        frozen_tests = _test_paths(canonical, old_config)
        candidate_tests = _test_paths(candidate, new_config)
        for name in sorted(frozen_tests):
            if name not in before or before.get(name) != after.get(name):
                errors.append(f"frozen test source changed or missing: {name}")
            elif (canonical / name).read_bytes() != (candidate / name).read_bytes():
                errors.append(f"frozen test source target changed: {name}")
        for name in sorted(candidate_tests):
            if not (candidate / name).is_file():
                errors.append(f"declared candidate test is missing: {name}")
        old_metrics = _test_metrics(canonical, frozen_tests)
        new_metrics = _test_metrics(candidate, candidate_tests)
        if new_metrics[0] < old_metrics[0] or new_metrics[1] < old_metrics[1]:
            errors.append("test function and assertion counts cannot decrease")
        if new_metrics[2] > old_metrics[2]:
            errors.append("repair cannot introduce skip or xfail")
        if new_metrics[3] > old_metrics[3]:
            errors.append("repair cannot introduce unconditional success assertions")
        old_commands = _string_list(old_config.get("test_commands"), "original test_commands", errors)
        new_commands = _string_list(new_config.get("test_commands"), "candidate test_commands", errors)
        if not old_commands or not new_commands:
            errors.append("test_commands cannot be empty")
        tokenized = [shlex.split(command) for command in new_commands]
        for command in old_commands:
            tokens = shlex.split(command)
            if not tokens or not any(_subsequence(tokens, new) for new in tokenized):
                errors.append(f"original test command token sequence was removed: {command}")
        for command in new_commands:
            if _BYPASS.search(command):
                errors.append("test command contains a forbidden reward or success bypass")
        old_count, new_count = old_config.get("test_case_count"), new_config.get("test_case_count")
        if type(old_count) is not int or type(new_count) is not int or old_count <= 0 or new_count <= 0:
            errors.append("test_case_count must be a positive integer")
        elif new_count < old_count:
            report = trusted_report if isinstance(trusted_report, dict) else {}
            fields = ("observed_total", "passed", "failed", "errors")
            if (any(type(report.get(field)) is not int for field in fields)
                    or report.get("observed_total") != new_count or report.get("passed") != new_count
                    or report.get("failed") != 0 or report.get("errors") != 0):
                errors.append("lower test_case_count requires trusted all-passed observed_total equal to new count")
    except (OSError, ValueError, TypeError, SyntaxError) as exc:
        errors.append(f"invalid test configuration or source: {exc}")

    try:
        archives = []
        for root in (canonical, candidate):
            task = tomllib.loads((root / "task.toml").read_text())
            archive = task.get("environment", {}).get("docker_image_archive")
            if not isinstance(archive, str) or not archive:
                raise ValueError("missing environment.docker_image_archive")
            archives.append(archive)
        if archives[0] != archives[1]:
            errors.append("task.toml image archive target must remain unchanged")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        errors.append(f"invalid task.toml: {exc}")

    docker_changed = "environment/Dockerfile" in changed
    old_docker = new_docker = None
    try:
        old_docker = (canonical / "environment/Dockerfile").read_bytes()
        new_docker = (candidate / "environment/Dockerfile").read_bytes()
        docker_changed = docker_changed or old_docker != new_docker
    except OSError as exc:
        errors.append(f"cannot read Dockerfile: {exc}")
    if docker_changed != bool(commands):
        errors.append("Dockerfile changes and nonempty image_commands must occur together")
    for command in commands:
        if "\n" in command or "\r" in command or _IMAGE_FORBIDDEN.search(command) or _BYPASS.search(command):
            errors.append("image_commands must be single-line and avoid private assets or privileged commands")
    if commands and old_docker is not None and new_docker is not None:
        try:
            old = _docker_instructions(old_docker.decode())
            new = _docker_instructions(new_docker.decode())
            additions = new[len(old):]
            if (not new_docker.startswith(old_docker) or new[:len(old)] != old
                    or additions != [("RUN", command.strip()) for command in commands]):
                errors.append("Dockerfile repair must append exactly the declared RUN image_commands")
        except (OSError, ValueError) as exc:
            errors.append(f"invalid Dockerfile repair: {exc}")
    return {"passed": not errors, "errors": errors, "changed_files": changed}


class RepairTransaction:
    """Switch an instance and image together, retaining recoverable backups.

    Journal states precede mutations. Reopen with the same three constructor
    paths after interruption and inspect ``state``; call ``rollback()`` for an
    unfinished switch, or ``commit()`` to finish an interrupted commit. This is
    recoverable across processes, not an atomic transaction across filesystems.
    Concurrent writers to the same instance are unsupported and detected by hashes.
    """

    _FINAL = {"committed", "rolled_back"}

    def __init__(self, instance_dir: Path, image_archive: Path, journal_path: Path):
        for path in (instance_dir, image_archive, journal_path):
            if Path(path).is_symlink():
                raise ValueError("transaction endpoints must not be symlinks")
        self.instance_dir = Path(instance_dir).resolve()
        self.image_archive = Path(image_archive).resolve()
        self.journal_path = Path(journal_path).resolve()
        if self.image_archive.is_relative_to(self.instance_dir) or self.journal_path.is_relative_to(self.instance_dir):
            raise ValueError("image and journal must be outside the instance directory")
        if self.image_archive == self.journal_path:
            raise ValueError("image and journal paths must differ")
        self._journal: dict = {}
        if self.journal_path.exists():
            self._journal = json.loads(self.journal_path.read_text())
            if (self._journal.get("schema_version") != "nl2repo.repair_transaction.v1"
                    or self._journal.get("instance_dir") != str(self.instance_dir)
                    or self._journal.get("image_archive") != str(self.image_archive)
                    or not re.fullmatch(r"[0-9a-f]{32}", self._journal.get("transaction_id", ""))):
                raise ValueError("journal belongs to a different or malformed transaction")

    @property
    def state(self) -> str:
        return self._journal.get("state", "new")

    def _path(self, kind: str) -> Path:
        endpoint = self.instance_dir if kind.startswith("instance") else self.image_archive
        suffix = "backup" if kind.endswith("backup") else "staged"
        return endpoint.with_name(f".{endpoint.name}.repair-{self._journal['transaction_id']}.{suffix}")

    def _persist(self, state: str) -> None:
        self._journal["state"] = state
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.journal_path.with_name(f".{self.journal_path.name}.{self._journal['transaction_id']}.tmp")
        if temporary.is_symlink() or self.journal_path.is_symlink():
            raise ValueError("journal path unexpectedly became a symlink")
        with temporary.open("w") as handle:
            json.dump(self._journal, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _durable_replace(temporary, self.journal_path)

    @staticmethod
    def _exists(path: Path) -> bool:
        return path.exists() or path.is_symlink()

    @staticmethod
    def _remove(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    def _matches(self, path: Path, key: str, directory: bool) -> bool:
        if path.is_symlink() or not (path.is_dir() if directory else path.is_file()):
            return False
        return (_tree_hash(path) if directory else _file_hash(path)) == self._journal.get(key)

    def switch(self, candidate_dir: Path, candidate_image: Path) -> None:
        if self.state not in self._FINAL | {"new"}:
            raise RuntimeError(f"unfinished repair transaction: {self.state}; recover it first")
        candidate_dir, candidate_image = Path(candidate_dir).resolve(), Path(candidate_image).resolve()
        if not self.instance_dir.is_dir() or not self.image_archive.is_file():
            raise ValueError("canonical instance and image must already exist")
        if not candidate_dir.is_dir() or not candidate_image.is_file():
            raise ValueError("candidate instance and image must already exist")
        if (candidate_dir.is_relative_to(self.instance_dir.resolve())
                or self.instance_dir.resolve().is_relative_to(candidate_dir)
                or candidate_image == self.image_archive.resolve()
                or candidate_image.is_relative_to(self.instance_dir.resolve())):
            raise ValueError("candidate inputs must be separate from canonical instance and image")
        _snapshot(self.instance_dir)
        _snapshot(candidate_dir)
        self._journal = {
            "schema_version": "nl2repo.repair_transaction.v1", "transaction_id": uuid.uuid4().hex,
            "instance_dir": str(self.instance_dir), "image_archive": str(self.image_archive),
            "original_instance_hash": _tree_hash(self.instance_dir),
            "original_image_hash": _file_hash(self.image_archive),
            "candidate_instance_hash": _tree_hash(candidate_dir),
            "candidate_image_hash": _file_hash(candidate_image),
        }
        for kind in ("instance_backup", "image_backup", "instance_staged", "image_staged"):
            if self._exists(self._path(kind)):
                raise FileExistsError(f"refusing to overwrite transaction path: {self._path(kind)}")
        self._persist("staging")
        try:
            shutil.copytree(candidate_dir, self._path("instance_staged"), symlinks=True)
            shutil.copy2(candidate_image, self._path("image_staged"))
            # Absolute links back into the input candidate cannot survive a
            # relocation while retaining the instance containment guarantee.
            _snapshot(self._path("instance_staged"))
            if not self._matches(self._path("instance_staged"), "candidate_instance_hash", True):
                raise RuntimeError("candidate instance changed during staging")
            if not self._matches(self._path("image_staged"), "candidate_image_hash", False):
                raise RuntimeError("candidate image changed during staging")
            if not self._matches(self.instance_dir, "original_instance_hash", True):
                raise RuntimeError("canonical instance changed during staging")
            if not self._matches(self.image_archive, "original_image_hash", False):
                raise RuntimeError("canonical image changed during staging")
            _flush_tree(self._path("instance_staged"))
            with self._path("image_staged").open("rb") as handle:
                os.fsync(handle.fileno())
            _fsync_directory(self._path("image_staged").parent)
            for phase, source, destination in (
                ("backing_up_instance", self.instance_dir, self._path("instance_backup")),
                ("backing_up_image", self.image_archive, self._path("image_backup")),
                ("installing_instance", self._path("instance_staged"), self.instance_dir),
                ("installing_image", self._path("image_staged"), self.image_archive),
            ):
                self._persist(phase)
                _durable_replace(source, destination)
            self._persist("switched")
        except BaseException as exc:
            try:
                self.rollback()
            except BaseException as rollback_error:
                exc.add_note(f"automatic rollback incomplete: {rollback_error}; recover using {self.journal_path}")
            raise

    def commit(self) -> None:
        if self.state == "committed":
            return
        if self.state not in {"switched", "committing"}:
            raise RuntimeError(f"cannot commit transaction in state {self.state}")
        if not self._matches(self.instance_dir, "candidate_instance_hash", True) or not self._matches(self.image_archive, "candidate_image_hash", False):
            raise RuntimeError("candidate endpoints changed; refusing to commit")
        for kind, key, directory in (("instance_backup", "original_instance_hash", True), ("image_backup", "original_image_hash", False)):
            backup = self._path(kind)
            if self._exists(backup):
                if not self._matches(backup, key, directory):
                    raise RuntimeError("original backup changed; refusing to remove it")
            elif self.state != "committing":
                raise RuntimeError("original backup missing; refusing to commit")
        self._persist("committing")
        for kind in ("instance_backup", "image_backup", "instance_staged", "image_staged"):
            self._remove(self._path(kind))
        _fsync_directory(self.instance_dir.parent)
        _fsync_directory(self.image_archive.parent)
        self._persist("committed")

    def rollback(self) -> None:
        if self.state in {"new", "rolled_back"}:
            return
        if self.state in {"committed", "committing"}:
            raise RuntimeError("commit has started; original backups may already be removed")
        self._persist("rolling_back")
        failures = []
        for endpoint, prefix, directory in ((self.instance_dir, "instance", True), (self.image_archive, "image", False)):
            backup, staged = self._path(prefix + "_backup"), self._path(prefix + "_staged")
            try:
                if self._exists(backup):
                    if not self._matches(backup, "original_" + prefix + "_hash", directory):
                        raise RuntimeError(f"original {prefix} backup changed")
                    if self._exists(endpoint):
                        if not self._matches(endpoint, "candidate_" + prefix + "_hash", directory):
                            raise RuntimeError(f"{prefix} endpoint changed outside this transaction")
                        if self._exists(staged):
                            raise RuntimeError(f"{prefix} staging path unexpectedly occupied")
                        _durable_replace(endpoint, staged)
                    _durable_replace(backup, endpoint)
                elif not self._matches(endpoint, "original_" + prefix + "_hash", directory):
                    raise RuntimeError(f"original {prefix} and backup unavailable")
                self._remove(staged)
                _fsync_directory(endpoint.parent)
            except (OSError, ValueError, RuntimeError) as exc:
                failures.append(str(exc))
        if failures:
            raise RuntimeError("rollback incomplete: " + "; ".join(failures))
        self._persist("rolled_back")
