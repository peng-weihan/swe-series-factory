"""Strict JUnit parsing; copied into exported tasks without package dependencies."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

RANK = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}


def parse_junit(path: Path) -> dict:
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        return {"healthy": False, "tests": {}, "errors": [str(exc)]}
    tests, errors = {}, []
    for case in tree.iter("testcase"):
        name, cls = case.get("name", ""), case.get("classname", "")
        filename = case.get("file") or cls.replace(".", "/") + ".py"
        if not name or not cls:
            errors.append("Test case has no unambiguous classname/name")
            continue
        module = filename.removesuffix(".py").removeprefix("./").replace("/", ".")
        suffix = cls.removeprefix(module + ".") if cls.startswith(module + ".") else ""
        node = filename.removeprefix("./") + ("::" + suffix.replace(".", "::") if suffix else "") + "::" + name
        state = next((s for s in ("error", "failed", "skipped")
                      if case.find({"failed": "failure"}.get(s, s)) is not None), "passed")
        if node not in tests or RANK[state] > RANK[tests[node]]:
            tests[node] = state
    for suite in tree.iter("testsuite"):
        if int(suite.get("errors", "0")):
            errors.append("Suite reports errors (including collection/setup failures)")
    if not tests:
        errors.append("No test cases executed")
    if "error" in tests.values():
        errors.append("A test reported an execution error")
    return {"healthy": not errors, "tests": tests, "errors": errors}


def score(tests: dict, f2p: list[str], p2p: list[str]) -> float:
    return float(bool(f2p) and all(tests.get(node) == "passed" for node in f2p + p2p))


def red_green(before: dict, after: dict, *, required: list[str] | None = None) -> dict:
    a, b = before["tests"], after["tests"]
    f2p = sorted(k for k in a.keys() & b.keys() if a[k] == "failed" and b[k] == "passed")
    p2p = sorted(k for k in a.keys() & b.keys() if a[k] == b[k] == "passed")
    required = required or sorted(a.keys() | b.keys())
    errors = []
    if not before["healthy"] or not after["healthy"]:
        errors.append("Unhealthy native test execution")
    if not f2p or not p2p:
        errors.append("Both F2P and P2P must be nonempty")
    if any(b.get(node) != "passed" for node in required):
        errors.append("Required tests are missing/skipped/failing in gold")
    if any(a.get(node) not in {"passed", "failed"} for node in required):
        errors.append("Required tests are missing/skipped/errored in problem state")
    return {"passed": not errors, "fail_to_pass": f2p, "pass_to_pass": p2p, "errors": errors,
            "base_missing_ids": sorted(set(required) - a.keys())}
