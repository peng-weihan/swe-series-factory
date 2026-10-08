"""Stage contracts and Oracle reward normalization."""
from __future__ import annotations

import math
import re
from typing import Any


def positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def validate_a1(result: dict) -> dict:
    errors = []
    for key in ("env_spec", "oracle_report", "docker", "commands"):
        if not isinstance(result.get(key), dict):
            errors.append(f"{key} must be an object")
    if errors:
        return {"passed": False, "errors": errors}
    env, report = result["env_spec"], result["oracle_report"]
    for key in ("install_commands", "test_commands"):
        value = env.get(key)
        if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
            errors.append(f"env_spec.{key} must be an array of nonempty strings")
    if not env.get("test_commands"):
        errors.append("test_commands must not be empty")
    if result.get("status") == "agent1_passed":
        for key, expected in (("ok", True), ("returncode", 0), ("failed", 0), ("errors", 0), ("network_none", True)):
            value = report.get(key)
            if value != expected or (key in ("ok", "network_none") and value is not True):
                errors.append(f"oracle_report.{key} must equal {expected!r}")
        for key in ("passed", "collected"):
            if not positive_int(report.get(key)):
                errors.append(f"oracle_report.{key} must be a positive integer (not bool)")
        tests = env.get("test_files")
        if not isinstance(tests, list) or not tests or not all(isinstance(x, str) and x for x in tests):
            errors.append("env_spec.test_files must contain paths")
        command = result["commands"].get("oracle_run", "")
        if not isinstance(command, str) or not re.search(r"(?:^|\s)--network(?:\s+|=)none(?=\s|$)", command):
            errors.append("commands.oracle_run must use --network none or --network=none")
        if not isinstance(result["docker"].get("image"), str) or not result["docker"]["image"].strip():
            errors.append("docker.image must name the built dependency image")
    else:
        errors.append("agent did not declare agent1_passed")
    return {"passed": not errors, "errors": errors}


def normalize_reward(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if not isinstance(value, dict):
        return None
    for key in ("reward", "score", "mean"):
        if key in value:
            result = normalize_reward(value[key])
            if result is not None:
                return result
    values = [float(x) for x in value.values() if type(x) in (int, float) and math.isfinite(x)]
    return values[0] if len(values) == 1 else None


def extract_reward(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("verifier_result", "rewards"):
            if key in value:
                return extract_reward(value[key])
    return normalize_reward(value)


INFRA_PATTERNS = (
    "connection refused", "connection reset", "temporarily unavailable", "ssh: connect",
    "no space left on device", "cannot connect to the docker daemon", "docker daemon is not running",
    "context deadline", "invalid reference format", "failed to tag", "error during connect",
)


def is_infrastructure_error(text: str) -> bool:
    return any(pattern in text.lower() for pattern in INFRA_PATTERNS)
