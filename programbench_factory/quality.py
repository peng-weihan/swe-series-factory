from __future__ import annotations

import ast
import hashlib
import re
import shutil
from pathlib import Path

from nl2repo_factory.common import FactoryError


def branch_id(commit: str, name: str) -> str:
    return name if re.fullmatch(r"[0-9a-f]{12}", name) else hashlib.sha256((commit + ":" + name).encode()).hexdigest()[:12]


def flatten(branches: Path, destination: Path, commit: str) -> dict:
    destination.mkdir(parents=True, exist_ok=False)
    result, used = {}, set()
    for branch in sorted(branches.iterdir()):
        if not branch.is_dir() or branch.is_symlink():
            raise FactoryError("Every branch must be a real directory")
        bid = branch_id(commit, branch.name)
        if bid in result:
            raise FactoryError("Branch hash collision")
        entry = {"name": branch.name, "ignored": False, "ignore_reason": "", "tests": [], "mapping": {}, "origins": {}, "ignored_tests": []}
        for source in sorted(branch.rglob("*")):
            if source.is_symlink():
                raise FactoryError("Test symlinks are forbidden")
            if not source.is_file():
                continue
            if not source.name.startswith("test_") or source.suffix != ".py":
                raise FactoryError("Branches may contain only self-contained test_*.py files")
            tree = ast.parse(source.read_text())
            names = []
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and (node.name.startswith("Test") or any(getattr(c, "name", "").startswith("test_") for c in node.body)):
                    raise FactoryError("Class tests are not supported by the baseline mapping")
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    if node.decorator_list or isinstance(node, ast.AsyncFunctionDef):
                        raise FactoryError("Baseline requires undecorated synchronous top-level tests")
                    names.append(node.name)
            if not names or len(names) != len(set(names)):
                raise FactoryError("Empty or duplicate test definitions")
            stem = "test_" + bid[:6] + "_" + source.stem.removeprefix("test_")
            while stem in used:
                stem += "_x"
            used.add(stem)
            shutil.copy2(source, destination / (stem + ".py"))
            for name in names:
                dotted = f"tests.{stem}.{name}"
                entry["tests"].append(dotted)
                entry["mapping"][dotted] = f"tests/{stem}.py::{name}"
                entry["origins"][dotted] = f"{branch.name}/{source.relative_to(branch).as_posix()}::{name}"
        if not entry["tests"]:
            raise FactoryError("Empty test branch")
        result[bid] = entry
    if not result:
        raise FactoryError("No test branches")
    return result


def classify(branches: dict, dummy: dict, gold: dict, *, min_kept=8) -> dict:
    if not dummy["healthy"] or not gold["healthy"]:
        raise FactoryError("QC execution unhealthy; inspect collection, dependencies and runner logs")
    expected = {v for b in branches.values() for v in b["mapping"].values()}
    if (set(dummy["tests"]) | set(gold["tests"])) - expected:
        raise FactoryError("Collected IDs are not represented by the test manifest")
    kept, ignored = [], []
    for branch in branches.values():
        branch["ignored_tests"] = []
        for name, native in branch["mapping"].items():
            d, g = dummy["tests"].get(native), gold["tests"].get(native)
            reason = ("slow_or_hang" if d is None or g is None else "dummy_pass" if d == "passed"
                      else "gold_fail" if g != "passed" else "dummy_invalid" if d != "failed" else None)
            if reason:
                branch["ignored_tests"].append({"name": name, "reasons": [{"id": reason}]})
                ignored.append(name)
            else:
                kept.append(native)
        branch["ignored"] = len(branch["ignored_tests"]) == len(branch["tests"])
        branch["ignore_reason"] = "no_effective_tests" if branch["ignored"] else ""
    return {"passed": len(kept) >= min_kept, "kept_native_ids": sorted(kept), "n_kept": len(kept),
            "n_tests": len(expected), "n_branches": len(branches), "ignored": ignored}


def difficulty(help_text: str, branches: int, kept: int) -> dict:
    flags = len(set(re.findall(r"--[a-zA-Z][a-zA-Z0-9-]*", help_text)))
    # Documented heuristic: indented bare words with a description are suspected subcommands.
    subcommands = len(set(re.findall(r"(?m)^\s{2,}([a-z][a-z0-9_-]*)\s{2,}\S", help_text)))
    value = flags + 4 * subcommands + branches + kept // 30
    label = ("unrated" if subcommands == 0 and flags <= 2 and kept < 20 else
             "hard" if value >= 45 or subcommands >= 8 else
             "medium" if value >= 18 or subcommands >= 2 else "easy")
    return {"difficulty": label, "flags": flags, "subcommands": subcommands, "score": value}
