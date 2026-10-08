"""Bounded claims for a rebuilt runtime: source hashes, history, gold permissions."""
from __future__ import annotations

import hashlib
import re
import tarfile
from pathlib import PurePosixPath

from nl2repo_factory.common import FactoryError


def check_recipe(text: str, commit: str):
    stages, count = set(), 0
    for line in text.splitlines():
        if not re.match(r"(?i)^\s*FROM\s", line):
            continue
        parts = line.strip().split()
        values = [v for v in parts[1:] if not v.startswith("--")]
        if not values:
            raise FactoryError("Invalid FROM instruction")
        image = values[0]
        if image != "scratch" and image.lower() not in stages and not re.search(r"@sha256:[0-9a-f]{64}$", image):
            raise FactoryError("R2 requires digest-pinned external base images")
        if len(values) >= 3 and values[1].lower() == "as":
            stages.add(values[2].lower())
        count += 1
    if not count or commit not in text:
        raise FactoryError("R2 recipe must bind the full source SHA")
    if "/opt/programbench-stashed-executable-do-not-modify" not in text or "/opt/programbench-source-commit" not in text:
        raise FactoryError("R2 recipe must contain gold stash and actual source-commit evidence")


def inspect_cleanroom(archive, source_hashes, runtime_user):
    errors, findings = [], []
    try:
        uid = int(runtime_user.split(":")[0])
    except (ValueError, AttributeError):
        uid = 0
    if uid <= 0:
        errors.append("Runtime USER must be an explicit non-root numeric UID")
    gold = {}
    with tarfile.open(archive, "r|*") as tar:
        for member in tar:
            name = member.name.removeprefix("./").lstrip("/")
            if ".git" in PurePosixPath(name).parts:
                findings.append({"path": name, "reason": "git_history"})
            if name in {"workspace/executable", "opt/programbench-stashed-executable-do-not-modify"}:
                gold[name] = {"regular": member.isfile(), "mode": member.mode, "uid": member.uid}
            if member.isfile():
                stream = tar.extractfile(member)
                h = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
                if h.hexdigest() in source_hashes:
                    findings.append({"path": name, "reason": "reference_source_content"})
    if findings:
        errors.append("Runtime contains source content or Git history")
    for name in ("workspace/executable", "opt/programbench-stashed-executable-do-not-modify"):
        entry = gold.get(name)
        if (not entry or not entry["regular"] or entry["uid"] != 0 or entry["mode"] & 0o666
                or not entry["mode"] & 0o001):
            errors.append(f"Gold must be root-owned, execute-only for the solver: {name}")
    return {"passed": not errors, "errors": errors, "findings": findings, "runtime_user": runtime_user,
            "gold_permissions": gold,
            "scope": "Merged runtime filesystem exact source/hidden-test content hashes, Git paths and declared gold permissions; not R3 anti-copy/wrapper validation or a historical-layer audit"}
