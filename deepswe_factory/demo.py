"""Synthetic, executable fixtures; no model or platform-success claims."""
from __future__ import annotations

from pathlib import Path

from nl2repo_factory.common import write_json
from swe_factory.runtime import Runner
from swe_factory.snapshots import copy_tree, diff, initialize
from .pipeline import Pipeline

FULL = 'def combine(parent, child):\n    return {**parent, **child}\n\ndef plain(value):\n    return dict(value)\n'
BROKEN = 'def combine(parent, child):\n    return {}\n\ndef plain(value):\n    return dict(value)\n'
TARGET = '''from calc import combine
def test_parent():
    assert combine({"a": 1}, {}) == {"a": 1}
def test_child():
    assert combine({}, {"b": 2}) == {"b": 2}
def test_override():
    assert combine({"a": 1, "b": 2}, {"a": 3}) == {"a": 3, "b": 2}
def test_unicode():
    assert combine({"名字": "甲"}, {"名字": "乙"}) == {"名字": "乙"}
'''
REGRESSION = 'from calc import plain\ndef test_plain():\n    assert plain({"a": 1}) == {"a": 1}\n'
BOUNDARY = 'from calc import combine\ndef test_none_value():\n    assert combine({"a": 1}, {"a": None}) == {"a": None}\n'
MANY = '\ndef merge_many(*layers):\n    result = {}\n    for layer in layers:\n        result.update(layer)\n    return result\n'
MASKED_MANY = '\ndef merge_many(*layers):\n    return {}\n'
SPECIAL = '''from calc import merge_many
def test_one_layer():
    assert merge_many({"a": 1}) == {"a": 1}
def test_three_layers():
    assert merge_many({"a": 1}, {"b": 2}, {"a": 3}) == {"a": 3, "b": 2}
def test_empty_layer():
    assert merge_many({}, {"b": 2}, {}) == {"b": 2}
'''
INSTRUCTION = 'Implement combine(parent, child) to return a new dictionary containing both mappings, with child values winning on duplicate keys. Preserve plain(value).'


def plan(*targets):
    return {"target_command": ["{python}", "-m", "pytest", *targets, "-q"],
            "regression_command": ["{python}", "-m", "pytest", "tests/test_regression.py", "-q"], "build_command": []}


class DemoAgent:
    synthetic = True

    def run(self, role, prompt, inputs, workspace, attempt_dir, timeout):
        attempt_dir.mkdir(parents=True)
        result = {"status": "ok"}
        if role == "analyze":
            result.update(summary="Synthetic mapping precedence fixture", evidence_paths=["calc.py", "tests/test_regression.py"])
        elif role == "ablate":
            (workspace / "calc.py").write_text(BROKEN)
            (workspace / "tests/test_target.py").unlink()
            result.update(instruction=INSTRUCTION, **plan("tests/test_target.py"))
        elif role == "query":
            result.update(instruction=INSTRUCTION, requirements=["R1: parent then child, child overrides"], evidence_paths=["calc.py", "tests/test_regression.py"])
        elif role == "testcase":
            (workspace / "tests/test_target.py").write_text(TARGET)
            result.update(**plan("tests/test_target.py"))
        elif role == "critic":
            result.update(verdict="valid", failure_matches_query=True, reason="Reported assertion failures compare empty dict with requested merge output")
        elif role == "solution":
            (workspace / "calc.py").write_text(FULL)
            result["build_command"] = []
        elif role == "augment":
            (workspace / "tests/test_boundary.py").write_text(BOUNDARY)
        elif role == "mask":
            if inputs["purpose"] == "base":
                (workspace / "calc.py").write_text(BROKEN)
                (workspace / "tests/test_target.py").unlink()
                (workspace / "tests/test_boundary.py").unlink()
                result.update(instruction=INSTRUCTION, **plan("tests/test_target.py", "tests/test_boundary.py"))
            else:
                (workspace / "calc.py").write_text(FULL + MASKED_MANY)
                (workspace / "tests/test_specialized.py").unlink()
                result.update(instruction="Implement merge_many(*layers), combining all dictionaries from left to right. Later values override earlier ones. Preserve combine and plain.", **plan("tests/test_specialized.py"))
                result["regression_command"] = ["{python}", "-m", "pytest", "tests/test_regression.py", "tests/test_target.py", "-q"]
        elif role == "plan":
            result.update(surface_gap="Arbitrary numbers of layers", transferred_mechanisms=["precedence"], requirements=["R1: left-to-right arbitrary-layer merging"], instruction="Implement merge_many(*layers).")
        elif role == "evolve":
            (workspace / "calc.py").write_text(FULL + MANY)
            (workspace / "tests/test_specialized.py").write_text(SPECIAL)
        elif role == "rewrite":
            result.update(instruction=inputs["instruction"], requirements=inputs["requirements"])
        else:
            raise AssertionError(role)
        write_json(attempt_dir / "synthetic_response.json", result)
        return result


def run_demo(output: Path, route="all"):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = {}
    for selected in (["ablation", "query", "specialize"] if route == "all" else [route]):
        source = output / (selected + "-source")
        (source / "tests").mkdir(parents=True)
        (source / "calc.py").write_text(FULL if selected == "ablation" else BROKEN)
        (source / "tests/test_regression.py").write_text(REGRESSION)
        if selected == "ablation":
            (source / "tests/test_target.py").write_text(TARGET)
        commit = initialize(source)
        candidate = {"route": selected, "local_path": str(source), "commit": commit,
                     "test_paths": ["tests"], "production_paths": ["calc.py"]}
        if selected == "specialize":
            reference = output / "reference"
            task = reference / "reference-001"
            full = copy_tree(source, output / "reference-code")
            (full / "calc.py").write_text(FULL)
            tested = copy_tree(source, output / "reference-tests")
            (tested / "tests/test_target.py").write_text(TARGET)
            assets = {"solution/solution.patch": diff(source, full), "tests/test.patch": diff(source, tested),
                      "instruction.md": INSTRUCTION, "tests/test.sh": "#!/bin/sh\nexit 0\n", "tests/grader.py": "# Fixture reference metadata; reconstructed task uses real verifier.\n",
                      "environment/Dockerfile": "FROM python:3.12-slim\n", "tests/Dockerfile": "FROM python:3.12-slim\n",
                      "task.toml": f'[metadata]\nbase_commit = "{commit}"\n', "tests/config.json": "{}\n"}
            for name, text in assets.items():
                target = task / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            write_json(reference / "manifest.json", {"tasks": [{"task_id": "reference-001", "ext_id": "synthetic-reference"}]})
            candidate.update(reference_dir=str(reference), reference_task_id="reference-001")
        results[selected] = Pipeline(Runner(backend="host"), DemoAgent()).run(candidate, output / selected)
    summary = {"passed": all(v["status"] == "internally_verified" for v in results.values()), "synthetic_fixture": True,
               "model_called": False, "docker_executed": False, "outer_harbor_verified": False, "routes": results}
    write_json(output / "summary.json", summary)
    return summary
