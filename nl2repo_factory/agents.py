"""Adapter for an existing tool-capable CLI, with durable prompts and traces.

The CLI owns its tool loop. It receives the prompt on stdin and can use request,
response and workspace paths exposed via environment variables or argv placeholders.
No service credentials are stored in configuration or request artifacts.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Protocol

from .common import FactoryError, run_process, write_json


class AgentResponseError(FactoryError):
    pass


class Agent(Protocol):
    def run(self, role: str, prompt: str, inputs: dict, workspace: Path,
            attempt_dir: Path, timeout: float) -> dict: ...


def parse_response(text: str) -> dict:
    text = text.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        # Some CLI JSON envelopes put the final model text in result.
        if "status" not in value and isinstance(value.get("result"), str):
            return parse_response(value["result"])
        return value
    candidates = []
    decoder = json.JSONDecoder()
    position = 0
    while position < len(text):
        start = text.find("{", position)
        if start < 0:
            break
        try:
            candidate, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            position = start + 1
            continue
        position = end
        if isinstance(candidate, dict) and "status" in candidate:
            candidates.append(candidate)
    if candidates:
        return candidates[-1]
    raise AgentResponseError("Agent response has no JSON object with a status")


class CommandAgent:
    def __init__(self, argv: list[str], *, env: dict[str, str] | None = None,
                 stdin_prompt: bool = True):
        if not argv or not all(isinstance(x, str) and x for x in argv):
            raise ValueError("agent.argv must be a nonempty array of strings")
        self.argv = argv
        self.env = env or {}
        self.stdin_prompt = stdin_prompt

    def run(self, role: str, prompt: str, inputs: dict, workspace: Path,
            attempt_dir: Path, timeout: float) -> dict:
        attempt_dir.mkdir(parents=True, exist_ok=False)
        workspace.mkdir(parents=True, exist_ok=True)
        request_path, response_path = attempt_dir / "request.json", attempt_dir / "response.json"
        prompt_path = attempt_dir / "prompt.md"
        prompt_path.write_text(prompt)
        write_json(request_path, {"schema_version": "nl2repo.agent_request.v1", "role": role,
                                  "inputs": inputs, "workspace": str(workspace.resolve()),
                                  "response_path": str(response_path.resolve()), "prompt": prompt})
        variables = {"role": role, "request": str(request_path.resolve()),
                     "response": str(response_path.resolve()), "prompt": str(prompt_path.resolve()),
                     "workspace": str(workspace.resolve())}
        argv = []
        for argument in self.argv:
            for key, replacement in variables.items():
                argument = argument.replace("{" + key + "}", replacement)
            argv.append(argument)
        env = dict(os.environ, **self.env)
        env.update({"NL2REPO_" + key.upper(): value for key, value in variables.items()})
        execution = run_process(argv, cwd=workspace, timeout=timeout,
                                stdin=prompt if self.stdin_prompt else None, env=env)
        (attempt_dir / "stdout.txt").write_text(execution.stdout)
        (attempt_dir / "stderr.txt").write_text(execution.stderr)
        write_json(attempt_dir / "execution.json", {
            "returncode": execution.returncode, "timed_out": execution.timed_out,
            "elapsed_sec": execution.elapsed_sec,
        })
        if execution.timed_out:
            raise FactoryError(f"{role} agent timed out; see {attempt_dir}")
        if execution.returncode:
            raise FactoryError(f"{role} agent exited {execution.returncode}; see {attempt_dir}")
        raw = response_path.read_text() if response_path.is_file() else execution.stdout
        result = parse_response(raw)
        write_json(attempt_dir / "parsed_response.json", result)
        return result
