"""Immutable inputs and binary-safe, controller-generated patches."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from nl2repo_factory.common import FactoryError, require_process, run_process, sha256

IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".factory-report.xml"}


def files(root: Path) -> dict[str, dict]:
    result = {}
    for directory, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED)
        for name in dirs + sorted(names):
            path = Path(directory) / name
            if path.is_symlink():
                raise FactoryError(f"Snapshot symlinks are not supported: {path}")
            if path.is_file() and name not in IGNORED:
                result[path.relative_to(root).as_posix()] = {
                    "sha256": sha256(path), "executable": bool(path.stat().st_mode & 0o111)}
    return result


def digest(root: Path) -> str:
    import json
    return hashlib.sha256(json.dumps(files(root), sort_keys=True).encode()).hexdigest()


def copy_tree(source: Path, destination: Path) -> Path:
    if destination.exists() or destination.resolve().is_relative_to(source.resolve()):
        raise FactoryError(f"Snapshot destination must be new and outside source: {destination}")
    files(source)
    shutil.copytree(source, destination, ignore=lambda _, names: set(names) & IGNORED)
    return destination


def relative(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(p in {"..", ".git"} for p in path.parts) or "\\" in value:
        raise FactoryError(f"Unsafe relative path: {value!r}")
    if path.as_posix() in {".", ""}:
        raise FactoryError("Empty relative path")
    return path.as_posix()


def owned(path: str, roots: list[str]) -> bool:
    return any(path == relative(root) or path.startswith(relative(root).rstrip("/") + "/") for root in roots)


def changed(before: Path, after: Path) -> set[str]:
    a, b = files(before), files(after)
    return {p for p in a.keys() | b.keys() if a.get(p) != b.get(p)}


def git(root: Path, *args: str):
    return require_process(run_process(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.autocrlf=false",
                                       "-c", "commit.gpgsign=false", *args], cwd=root, timeout=120), "git " + args[0])


def initialize(root: Path) -> str:
    git(root, "init", "--quiet")
    git(root, "add", "--all", "--force")
    git(root, "-c", "user.name=Instance Factory", "-c", "user.email=factory@example.invalid",
        "commit", "--quiet", "--allow-empty", "-m", "Frozen input")
    return git(root, "rev-parse", "HEAD").stdout.strip()


def diff(before: Path, after: Path, paths: set[str] | None = None) -> str:
    changes = changed(before, after)
    if paths is not None:
        changes &= paths
    if not changes:
        return ""
    with tempfile.TemporaryDirectory(prefix="factory-diff-") as tmp:
        staging = copy_tree(before, Path(tmp) / "repo")
        initialize(staging)
        for name in sorted(changes, key=lambda p: (-len(Path(p).parts), p)):
            target = staging / name
            if target.is_file():
                target.unlink()
        for name in sorted(changes):
            source, target = after / name, staging / name
            if source.is_file():
                if target.is_dir():
                    # All contained files were explicit changes removed above.
                    for folder in sorted((p for p in target.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                        folder.rmdir()
                    target.rmdir()
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        git(staging, "add", "--all", "--force")
        return git(staging, "diff", "--cached", "--binary", "--no-ext-diff", "--no-renames", "HEAD").stdout


def apply(root: Path, patch: str) -> set[str]:
    if not patch.strip():
        raise FactoryError("Empty patch")
    baseline = files(root)
    # git apply without --unsafe-paths rejects absolute/traversing paths. A local
    # repository also prevents Git discovering an unrelated ancestor repository.
    if not (root / ".git").exists():
        initialize(root)
    with tempfile.TemporaryDirectory(prefix="factory-patch-") as tmp:
        path = Path(tmp) / "change.patch"
        path.write_text(patch)
        git(root, "apply", "--check", "--binary", str(path))
        git(root, "apply", "--binary", str(path))
    after = files(root)
    return {p for p in baseline.keys() | after.keys() if baseline.get(p) != after.get(p)}


def acquire(record: dict, destination: Path) -> dict:
    import re
    commit = record.get("commit", "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise FactoryError("A full 40-character commit SHA is required")
    source = record.get("local_path") or record.get("url")
    if not source:
        raise FactoryError("Candidate requires local_path or url")
    with tempfile.TemporaryDirectory(prefix="factory-source-") as tmp:
        clone = Path(tmp) / "repo"
        require_process(run_process(["git", "clone", "--no-hardlinks", "--", str(source), str(clone)], timeout=900), "Clone")
        git(clone, "checkout", "--detach", commit)
        actual = git(clone, "rev-parse", "HEAD").stdout.strip()
        if actual.lower() != commit.lower():
            raise FactoryError("Resolved commit differs from requested commit")
        copy_tree(clone, destination)
    return {"commit": actual, "source": source, "snapshot_sha256": digest(destination)}
