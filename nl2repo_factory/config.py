"""TOML configuration; paths are resolved relative to the configuration file."""
from __future__ import annotations

import tomllib
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    root: Path
    dataset_root: Path
    image_root: Path
    agent_argv: list[str] = field(default_factory=list)
    agent_stdin: bool = True
    docker_binary: str = "docker"
    docker_host: str | None = None
    agent_timeout: float = 3600
    specification_timeout: float = 1800
    repair_timeout: float = 1800
    build_timeout: float = 3600
    oracle_timeout: float = 3600
    agent1_retries: int = 1
    coverage_retries: int = 4
    image_leak_retries: int = 1
    oracle_infra_retries: int = 2
    repair_rounds: int = 3
    verify_empty: bool = True
    skip_oracle: bool = False

    @classmethod
    def load(cls, path: Path) -> "Config":
        document = tomllib.loads(path.read_text())
        allowed_sections = {"paths", "agent", "docker", "pipeline"}
        if set(document) - allowed_sections:
            raise ValueError(f"Unknown configuration sections: {sorted(set(document) - allowed_sections)}")
        base = path.resolve().parent

        def resolve(value: str) -> Path:
            p = Path(value).expanduser()
            return (base / p).resolve() if not p.is_absolute() else p.resolve()

        paths, agent, docker = (document.get(key, {}) for key in ("paths", "agent", "docker"))
        root = resolve(paths.get("root", "runs"))
        options = document.get("pipeline", {})
        allowed = set(cls.__dataclass_fields__) - {
            "root", "dataset_root", "image_root", "agent_argv", "agent_stdin", "docker_binary", "docker_host"}
        unknown = set(options) - allowed
        if unknown:
            raise ValueError(f"Unknown pipeline options: {sorted(unknown)}")
        result = cls(root=root,
                     dataset_root=resolve(paths.get("dataset", str(root / "dataset"))),
                     image_root=resolve(paths.get("images", str(root / "images"))),
                     agent_argv=agent.get("argv", []), agent_stdin=agent.get("stdin_prompt", True),
                     docker_binary=docker.get("binary", "docker"), docker_host=docker.get("host"), **options)
        for key in ("verify_empty", "skip_oracle", "agent_stdin"):
            if type(getattr(result, key)) is not bool:
                raise ValueError(f"{key} must be a boolean")
        for key in allowed:
            value = getattr(result, key)
            if key.endswith("timeout") and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{key} must be positive")
            if key.endswith("retries") or key == "repair_rounds":
                if type(value) is not int or value < 0:
                    raise ValueError(f"{key} must be a nonnegative integer")
        # Documented baseline normalization: zero is not a repair disable switch.
        result.repair_rounds = max(1, result.repair_rounds)
        return result
