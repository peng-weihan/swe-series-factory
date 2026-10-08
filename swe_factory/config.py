from __future__ import annotations

import math
import tomllib
from pathlib import Path

from nl2repo_factory.agents import CommandAgent
from nl2repo_factory.common import FactoryError
from .runtime import Runner


def load(path: Path) -> tuple[dict, Runner, CommandAgent | None]:
    data = tomllib.loads(path.read_text())
    if set(data) - {"runtime", "agent", "pipeline", "model"}:
        raise FactoryError("Unknown config section")
    runtime = data.get("runtime", {})
    for name in ("timeout",):
        value = runtime.get(name, 300)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise FactoryError(f"{name} must be positive and finite")
    pipeline = data.get("pipeline", {})
    if set(pipeline) - {"repair_rounds", "min_kept", "rebuild"}:
        raise FactoryError("Unknown pipeline setting")
    if type(pipeline.get("rebuild", True)) is not bool:
        raise FactoryError("rebuild must be a boolean")
    for key, default in (("repair_rounds", 3), ("min_kept", 8)):
        value = pipeline.get(key, default)
        if type(value) is not int or value < (0 if key == "repair_rounds" else 8):
            raise FactoryError(f"Invalid {key}")
    agent_data = data.get("agent", {})
    if agent_data and (not isinstance(agent_data.get("argv"), list) or not agent_data["argv"]):
        raise FactoryError("agent.argv must be a nonempty array")
    runner = Runner(**runtime)
    agent = CommandAgent(agent_data["argv"], stdin_prompt=agent_data.get("stdin_prompt", True)) if agent_data.get("argv") else None
    if data.get("model"):
        if agent is not None:
            raise FactoryError("Configure agent.argv OR model JSON-action engine, not both")
        from programbench_factory.actions import ActionAgent
        agent = ActionAgent(runner, **data["model"])
    return data, runner, agent


def records(path: Path) -> list[dict]:
    import json
    result = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise FactoryError(f"Invalid candidate at line {number}")
        for key in ("local_path", "reference_dir"):
            if row.get(key):
                value = Path(row[key]).expanduser()
                row[key] = str((path.resolve().parent / value).resolve())
        result.append(row)
    if not result:
        raise FactoryError("No candidates")
    return result
