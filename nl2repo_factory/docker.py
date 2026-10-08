"""Docker build/export and documented merged-filesystem leak inspection."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
import tempfile
import uuid
from pathlib import Path

from .common import FactoryError, require_process, run_process, write_json


def image_name(task_id: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9_.-]", "-", "teamfactory-instance-" + task_id.lower())).strip("-")


def inspect_filesystem(archive: Path, expected_start_sha256: str | None = None) -> dict:
    """Inspect docker export, not docker save; do not extract untrusted paths."""
    fatal, warnings = [], []
    start_digest = None
    with tarfile.open(archive, "r|*") as tar:
        for member in tar:
            if not member.isfile():
                continue
            name = member.name.removeprefix("./").lstrip("/")
            parts = Path(name).parts
            if parts and parts[0] in {"app", "repo", "src", "testbed"} and name.endswith(".py"):
                fatal.append({"reason": "visible_forbidden_source_path", "path": name})
            metadata = "site-packages" in parts and name.endswith(".dist-info/direct_url.json")
            pth = "site-packages" in parts and name.endswith(".pth")
            if metadata or pth or name == "workspace/start.md":
                stream = tar.extractfile(member)
                assert stream is not None
                if name == "workspace/start.md":
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                    start_digest = digest.hexdigest()
                    continue
                if member.size > 1024 * 1024:
                    fatal.append({"reason": "oversized_install_metadata", "path": name})
                    continue
                text = stream.read().decode("utf-8", errors="replace")
                if metadata:
                    try:
                        data = json.loads(text)
                    except (ValueError, TypeError):
                        warnings.append({"reason": "invalid_direct_url", "path": name})
                        continue
                    if isinstance(data, dict) and str(data.get("url", "")).startswith("file:"):
                        editable = isinstance(data.get("dir_info"), dict) and data["dir_info"].get("editable") is True
                        (warnings if editable else fatal).append({
                            "reason": "editable_file_direct_url" if editable else "noneditable_file_direct_url", "path": name})
                elif any(root in text for root in ("/workspace", "/testbed", "/repo", "/app", "/src")):
                    warnings.append({"reason": "pth_source_reference", "path": name})
    if expected_start_sha256 is not None and start_digest != expected_start_sha256:
        fatal.append({"reason": "start_md_digest_mismatch", "actual": start_digest, "expected": expected_start_sha256})
    return {"schema_version": "teamfactory.final_image_leak_check.v1", "passed": not fatal,
            "fatal_count": len(fatal), "hits": fatal[:50], "warnings": warnings[:50],
            "start_md_sha256": start_digest,
            "scope": "merged filesystem documented path/metadata rules; not a proof of complete source removal"}


class DockerAdapter:
    def __init__(self, binary: str = "docker", host: str | None = None, timeout: float = 3600):
        self.binary, self.timeout = binary, timeout
        self.env = dict(os.environ)
        if host:
            self.env["DOCKER_HOST"] = host

    def execute(self, arguments: list[str], *, timeout: float | None = None):
        return run_process([self.binary, *arguments], env=self.env, timeout=timeout or self.timeout)

    def preflight(self) -> dict:
        result = require_process(self.execute(["version", "--format", "{{.Server.Version}}"], timeout=30), "Docker preflight")
        return {"server_version": result.stdout.strip(), "binary": self.binary}

    def inspect(self, image: str) -> dict:
        result = require_process(self.execute(["image", "inspect", image]), "Image inspection")
        value = json.loads(result.stdout)[0]
        return {"id": value["Id"], "repo_digests": value.get("RepoDigests", [])}

    def build(self, context: Path, tag: str, log_dir: Path, dockerfile: Path | None = None) -> dict:
        arguments = ["build", "--tag", tag, "--file", str(dockerfile or context / "Dockerfile"), str(context)]
        result = self.execute(arguments)
        write_json(log_dir / "build.json", result.to_dict())
        require_process(result, "Docker build")
        return self.inspect(tag)

    def save(self, tag: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        try:
            require_process(self.execute(["save", "--output", str(temporary), tag]), "Image export")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, archive: Path) -> None:
        require_process(self.execute(["load", "--input", str(archive)]), "Image load")

    def leak_check(self, tag: str, log_dir: Path, expected_start_sha256: str | None = None) -> dict:
        log_dir.mkdir(parents=True, exist_ok=True)
        name = "nl2repo-inspect-" + uuid.uuid4().hex
        created = require_process(self.execute(["create", "--name", name, "--entrypoint", "/bin/true", tag]), "Inspection container creation")
        container = created.stdout.strip()
        try:
            with tempfile.TemporaryDirectory(prefix="image-scan-", dir=log_dir) as temporary:
                archive = Path(temporary) / "filesystem.tar"
                require_process(self.execute(["export", "--output", str(archive), container]), "Filesystem export")
                report = inspect_filesystem(archive, expected_start_sha256)
                write_json(log_dir / "leak_check.json", report)
                return report
        finally:
            self.execute(["rm", "-f", container], timeout=60)
