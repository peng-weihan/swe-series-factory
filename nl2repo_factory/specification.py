"""Deterministic specification gates; these do not establish semantic coverage."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from .ast_scan import is_test_asset


_DIRECTORY_HEADING = re.compile(r"^###\s+Project Directory Structure\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_TREE_ROW = re.compile(r"^(?P<prefix>[ │\t]*)(?P<branch>├──|└──|\+--|`--)\s*(?P<name>.+?)\s*$")
_NODE = re.compile(r"^### Node (\d+):[^\n]*$", re.MULTILINE)


def _tree_regions(lines: list[str]) -> list[tuple[int, int]]:
    regions = []
    in_section = False
    start = None
    fence = ""
    for index, line in enumerate(lines):
        if start is not None:
            match = _FENCE.match(line)
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) and not match.group(2).strip():
                regions.append((start, index))
                start = None
            continue
        if _DIRECTORY_HEADING.match(line):
            in_section = True
            continue
        if in_section and re.match(r"^#{1,3}\s", line):
            in_section = False
        match = _FENCE.match(line)
        if in_section and match and match.group(2).strip().lower() in {"", "text", "plaintext"}:
            start, fence = index + 1, match.group(1)
    return regions


def _tree_entries(lines: list[str], start: int, stop: int) -> list[dict[str, Any]]:
    entries = []
    parents: dict[int, str] = {}
    for index in range(start, stop):
        match = _TREE_ROW.match(lines[index])
        if not match:
            continue
        depth = len(match.group("prefix").expandtabs(4)) // 4 + 1
        name = re.split(r"\s+#\s*", match.group("name"), maxsplit=1)[0].strip().rstrip("/")
        path = "/".join([parents[level] for level in sorted(parents) if level < depth] + [name])
        for old_depth in list(parents):
            if old_depth >= depth:
                del parents[old_depth]
        parents[depth] = name
        entries.append({"line": index, "depth": depth, "name": name, "path": path, "match": match})
    return entries


def clean_start_md(text: str) -> str:
    """Remove test branches only from the documented implementation tree.

    Code and prose elsewhere are untouched. Do this before building the image;
    that ordering intentionally fixes the baseline's two-document discrepancy.
    """
    lines = text.splitlines()
    remove: set[int] = set()
    for start, stop in _tree_regions(lines):
        entries = _tree_entries(lines, start, stop)
        for entry in entries:
            if is_test_asset(entry["path"]):
                remove.add(entry["line"])
        remaining = [entry for entry in entries if entry["line"] not in remove]
        # Repair the connectors when deleting the last sibling branch.
        for index, entry in enumerate(remaining):
            following = next((other for other in remaining[index + 1:] if other["depth"] <= entry["depth"]), None)
            last = following is None or following["depth"] < entry["depth"]
            match = entry["match"]
            branch = ("└──" if last else "├──") if match.group("branch") in {"├──", "└──"} else ("`--" if last else "+--")
            line = lines[entry["line"]]
            lines[entry["line"]] = line[:match.start("branch")] + branch + line[match.end("branch"):]
    cleaned = "\n".join(line for index, line in enumerate(lines) if index not in remove)
    return cleaned + ("\n" if text.endswith("\n") else "")


def _aliases(symbol: str) -> set[str]:
    pieces = symbol.split(".")
    return {symbol, pieces[-1], ".".join(pieces[-2:])} - {""}


def validate_specification(
    start_md: str,
    manifest: dict,
    ast_payload: dict,
    node_count: int | None = None,
) -> dict[str, Any]:
    """Check coverage, numbered behavior nodes, implementation tree and budget.

    Short-name/full-text coverage deliberately follows the permissive baseline.
    An actual core list over 60 entries is rejected explicitly. A large required
    symbol list alone is not rejected: a class entry may cover many methods.
    Node field styles are not checked: strict template validation is disabled in
    the baseline and conflicts with its own prompt.
    """
    errors: list[str] = []
    if not isinstance(start_md, str) or not start_md.strip():
        return {"passed": False, "errors": ["start_md_empty"], "missing_symbols": []}
    if not isinstance(manifest, dict):
        return {"passed": False, "errors": ["manifest_not_object"], "missing_symbols": []}
    if not isinstance(ast_payload, dict):
        return {"passed": False, "errors": ["ast_payload_not_object"], "missing_symbols": []}
    claimed: set[str] = set()
    covered = manifest.get("covered_symbols", [])
    if not isinstance(covered, list):
        errors.append("covered_symbols_not_array")
        covered = []
    claimed.update(value for value in covered if isinstance(value, str) and value)
    core_apis = manifest.get("core_apis", [])
    if not isinstance(core_apis, list):
        errors.append("core_apis_not_array")
        core_apis = []
    for entry in core_apis:
        if not isinstance(entry, dict):
            errors.append("core_api_not_object")
            continue
        for key in ("name", "qualified_name"):
            if isinstance(entry.get(key), str) and entry[key]:
                claimed.add(entry[key])
        symbols = entry.get("covered_import_symbols", [])
        if isinstance(symbols, list):
            claimed.update(value for value in symbols if isinstance(value, str) and value)
    claimed_aliases = set().union(*(_aliases(symbol) for symbol in claimed)) if claimed else set()
    required = sorted({symbol for symbol in ast_payload.get("required_api_symbols", []) if isinstance(symbol, str) and symbol})
    missing = []
    for symbol in required:
        aliases = _aliases(symbol)
        if aliases.intersection(claimed_aliases):
            continue
        if any(re.search(r"(?<![\w])" + re.escape(alias) + r"(?![\w])", start_md) for alias in aliases):
            continue
        missing.append(symbol)
    if missing:
        errors.append("required_api_symbols_missing")

    nodes = list(_NODE.finditer(start_md))
    numbers = [int(match.group(1)) for match in nodes]
    if not nodes:
        errors.append("behavior_nodes_missing")
    elif numbers != list(range(1, len(numbers) + 1)):
        errors.append("behavior_nodes_not_contiguous")
    for index, node in enumerate(nodes):
        stop = nodes[index + 1].start() if index + 1 < len(nodes) else len(start_md)
        body = start_md[node.end():stop]
        next_heading = re.search(r"^#{1,3}\s", body, re.MULTILINE)
        if next_heading:
            body = body[:next_heading.start()]
        if not body.strip():
            errors.append(f"behavior_node_empty:{node.group(1)}")
    if node_count is not None and (isinstance(node_count, bool) or not isinstance(node_count, int) or node_count != len(nodes)):
        errors.append("reported_node_count_mismatch")

    lines = start_md.splitlines()
    regions = _tree_regions(lines)
    entries = [entry for start, stop in regions for entry in _tree_entries(lines, start, stop)]
    if not regions:
        errors.append("implementation_tree_missing")
    leaks = [entry["path"] for entry in entries if is_test_asset(entry["path"])]
    if leaks:
        errors.append("implementation_tree_test_assets")
    unsafe = [entry["path"] for entry in entries if PurePosixPath(entry["name"]).is_absolute() or ".." in PurePosixPath(entry["path"]).parts]
    if unsafe:
        errors.append("implementation_tree_unsafe_paths")
    python_count = sum(entry["name"].endswith(".py") for entry in entries)
    if python_count < 5:
        errors.append("implementation_python_file_count_lt_5")

    classes = {entry.get("qualified_name") for entry in ast_payload.get("public_classes", []) if isinstance(entry, dict) and isinstance(entry.get("qualified_name"), str)}
    required_groups = set()
    for symbol in required:
        parent_class = next((name for name in sorted(classes, key=len, reverse=True) if symbol == name or symbol.startswith(name + ".")), None)
        required_groups.add(parent_class or symbol)
    budget_exceeded = len(core_apis) > 60
    budget_conflict = budget_exceeded and len(required_groups) > 60
    if budget_exceeded:
        errors.append("core_api_budget_exceeded")
    if budget_conflict:
        errors.append("api_budget_conflict")
    source_imports = ast_payload.get("test_imported_repo_symbols", [])
    return {
        "passed": not errors, "errors": errors, "missing_symbols": missing,
        "missing_symbol_sources": [entry for entry in source_imports if isinstance(entry, dict) and entry.get("symbol") in missing],
        "node_count": len(nodes), "implementation_python_file_count": python_count,
        "test_asset_paths": leaks, "unsafe_paths": unsafe,
        "required_api_count": len(required), "required_core_group_count": len(required_groups),
        "core_api_count": len(core_apis), "api_budget_conflict": budget_conflict,
        "core_api_budget_exceeded": budget_exceeded,
    }
