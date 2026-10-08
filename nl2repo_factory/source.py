"""Repository identities and ordered JSONL selection (NL2Repo spec section 4)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


_SHORTHAND = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB = re.compile(r"github\.com/([^/?#]+)/([^/?#]+)")


def normalize_repo_url(value: str) -> str:
    """Preserve the baseline's branch order, including owner/repo.git."""
    if not isinstance(value, str):
        raise TypeError("repository URL must be a string")
    value = value.strip()
    if _SHORTHAND.fullmatch(value):
        return "https://github.com/" + value
    if value.startswith("git@github.com:"):
        return "https://github.com/" + value[len("git@github.com:"):].removesuffix(".git")
    return value.removesuffix(".git")


def make_task_id(url: str, suffix: str = "") -> str:
    """The URL, rather than its commit or contents, determines identity."""
    clean = normalize_repo_url(url).rstrip("/")
    match = _GITHUB.search(clean)
    if match:
        base = f"github-{match.group(1)}-{match.group(2)}"
    else:
        base = re.sub(r"[^A-Za-z0-9]+", "-", clean).strip("-")[:80] or "repo"
    base = re.sub(r"[^a-zA-Z0-9._-]", "-", base).strip("-").lower()
    digest = hashlib.sha1(clean.encode("utf-8")).hexdigest()[:7]
    result = f"{base}__{digest}"
    suffix = suffix.strip("-_")
    if suffix:
        # A suffix is also a directory component. Reject traversal instead of
        # silently changing the experiment's requested identity.
        if not re.fullmatch(r"[A-Za-z0-9._-]+", suffix) or suffix in {".", ".."}:
            raise ValueError("task suffix must contain only letters, digits, '.', '_' or '-'")
        result += "-" + suffix
    return result


task_id = make_task_id


def load_records(
    path: str | Path,
    start_index: int = 0,
    end_index: int = 0,
    reverse: bool = False,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Load valid candidates, slice, reverse, then limit, without deduplication.

    ``input_index`` is the zero-based index after URL-less rows are filtered and
    before selection. ``input_line`` preserves the physical one-based line.
    Malformed JSON and non-object records are input errors, not silent drops.
    Original fields are retained; the canonical URL and identity are refreshed.
    """
    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"{path}:{line_number}: each candidate must be a JSON object")
            selected = next(
                (item[key] for key in ("url", "html_url", "clone_url", "full_name") if item.get(key)),
                None,
            )
            if not isinstance(selected, str) or not selected.strip():
                continue
            normalized = normalize_repo_url(selected)
            record = dict(item)
            record.update(
                url=normalized,
                # Derive from the original string: normalizing owner/repo.git
                # twice would change the documented early-return behavior.
                task_id=make_task_id(selected),
                input_index=len(records),
                input_line=line_number,
            )
            records.append(record)
    stop = end_index if 0 < end_index <= len(records) else len(records)
    selected_records = records[start_index:stop]
    if reverse:
        selected_records.reverse()
    if limit > 0:
        selected_records = selected_records[:limit]
    return selected_records
