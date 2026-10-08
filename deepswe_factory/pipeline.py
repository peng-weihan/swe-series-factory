"""Three construction routes sharing strict replay and exported-verifier gates."""
from __future__ import annotations

import json
from pathlib import Path

from nl2repo_factory.common import FactoryError, read_json, write_json
from swe_factory.reports import red_green
from swe_factory.roles import Roles
from swe_factory.runtime import command
from swe_factory.snapshots import acquire, apply, changed, copy_tree, diff, digest, files, owned, relative
from .packaging import emit, selfcheck


class Revision(FactoryError):
    def __init__(self, role, reason):
        super().__init__(reason)
        self.role = role


def combined(*reports):
    result = {"healthy": all(r["healthy"] for r in reports), "tests": {}, "errors": []}
    for report in reports:
        result["errors"] += report["errors"]
        for key, value in report["tests"].items():
            if result["tests"].get(key, "passed") == "passed":
                result["tests"][key] = value
    return result


class Pipeline:
    def __init__(self, runner, agent=None, *, repair_rounds=3, agent_timeout=1800):
        self.runner, self.agent = runner, agent
        self.repair_rounds, self.agent_timeout = repair_rounds, agent_timeout

    def event(self, stage, **details):
        self.stage = stage
        self.events.append({"stage": stage, **details})
        write_json(self.output / "stages.json", self.events)

    def run(self, candidate: dict, output: Path) -> dict:
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.events, self.stage = [], "input"
        self.candidate = candidate
        write_json(self.output / "input.json", candidate)
        self.roles = Roles(self.agent, self.output / "roles", Path(__file__).with_name("prompts"), self.agent_timeout)
        try:
            self.test_paths = [relative(p) for p in candidate["test_paths"]]
            self.production_paths = [relative(p) for p in candidate["production_paths"]]
            if not self.test_paths or not self.production_paths:
                raise FactoryError("Explicit test and production path ownership is required")
            if any(owned(p, self.test_paths) or owned(t, [p]) for p in self.production_paths for t in self.test_paths):
                raise FactoryError("Production and test ownership overlap")
            self.source = acquire(candidate, self.output / "source")
            self.base = self.output / "source"
            self.event("source_frozen", source=self.source)
            route = candidate.get("route", "ablation")
            if route == "ablation":
                result = self.ablation()
            elif route == "query":
                result = self.forward()
            elif route == "specialize":
                result = self.specialize()
            else:
                raise FactoryError("route must be ablation, query, or specialize")
            summary = dict(result, route=route, status="internally_verified", level="L2", accepted=False,
                           outer_harbor_verified=False, model_called=self.agent is not None and not getattr(self.agent, "synthetic", False),
                           execution=self.runner.backend, source=self.source,
                           pending=["Target-platform submission capture and isolation", "Release-level semantic/leakage/strength review"])
            self.event("internally_verified", task=result["task_dir"])
        except (FactoryError, ValueError, KeyError, TypeError, OSError) as exc:
            summary = {"status": "failed", "stage": self.stage, "reason": str(exc), "accepted": False}
            write_json(self.output / "summary.json", summary)
            raise FactoryError(f"{self.stage}: {exc}; evidence: {self.output}") from exc
        write_json(self.output / "summary.json", summary)
        return summary

    def inputs(self, **extras):
        return {"source": self.source, "test_paths": self.test_paths, "production_paths": self.production_paths,
                "candidate": self.candidate, **extras}

    def check_ownership(self, paths: set[str], kind: str):
        roots = self.test_paths if kind == "test" else self.production_paths
        if not paths or any(not owned(p, roots) for p in paths):
            raise FactoryError(f"{kind} role changed empty or unowned paths: {sorted(paths)}")

    def plan(self, data: dict) -> dict:
        return {"target_command": command(data["target_command"]),
                "regression_command": command(data["regression_command"]),
                "build_command": command(data.get("build_command", []), empty=True),
                "test_paths": self.test_paths, "production_paths": self.production_paths,
                "timeout": self.runner.timeout}

    def matrix(self, problem: Path, solution: str, tests: str, plan: dict, directory: Path):
        directory.mkdir(parents=True, exist_ok=False)
        bt = copy_tree(problem, directory / "base-tests")
        self.check_ownership(apply(bt, tests), "test")
        gold = copy_tree(problem, directory / "gold")
        self.check_ownership(apply(gold, solution), "production")
        gt = copy_tree(gold, directory / "gold-tests")
        self.check_ownership(apply(gt, tests), "test")
        reports = {"C1": self.runner.suite(problem, plan["regression_command"], directory / "C1"),
                   "C2": self.runner.suite(bt, plan["target_command"], directory / "C2")}
        if plan["build_command"]:
            build = self.runner.run(gold, plan["build_command"], directory / "C3")
            reports["C3"] = {"passed": not build["execution"].timed_out and build["execution"].returncode == 0,
                              "status": "executed"}
            gold = build["workspace"]
            gt = copy_tree(gold, directory / "built-gold-tests")
            self.check_ownership(apply(gt, tests), "test")
        else:
            reports["C3"] = {"passed": True, "status": "not_applicable"}
        for label, state, key in (("C4", gold, "regression"), ("C5", gt, "target"), ("C6", gt, "regression")):
            reports[label] = self.runner.suite(state, plan[key + "_command"], directory / label)
        for label in ("C1", "C4", "C5", "C6"):
            report = reports[label]
            if not report["healthy"] or any(s != "passed" for s in report["tests"].values()):
                write_json(directory / "matrix.json", reports)
                if label == "C1":
                    raise FactoryError("Native baseline is unhealthy; repair the environment or input")
                raise Revision("testcase" if label == "C6" else "solution", f"{label} did not pass")
        if not reports["C3"]["passed"]:
            raise Revision("solution", "C3 build failed")
        gate = red_green(combined(reports["C1"], reports["C2"]), combined(reports["C5"], reports["C6"]))
        # C4 and C6 must actually run the same regression IDs as C1.
        native_ids = set(reports["C1"]["tests"])
        if any(set(reports[k]["tests"]) != native_ids for k in ("C4", "C6")):
            gate["passed"] = False
            gate["errors"].append("Regression test identities changed across states")
        write_json(directory / "matrix.json", reports)
        write_json(directory / "gate.json", gate)
        if not gate["passed"]:
            raise Revision("testcase", "; ".join(gate["errors"]))
        return gate

    def finish(self, name, problem, instruction, solution, tests, plan):
        if not isinstance(instruction, str) or not instruction.strip():
            raise FactoryError("Empty instruction")
        self.event(name + "_validation")
        directory = self.output / name
        gate = self.matrix(problem, solution, tests, plan, directory / "validation")
        config = dict(plan, fail_to_pass=gate["fail_to_pass"], pass_to_pass=gate["pass_to_pass"])
        task = directory / "task"
        emit(task, problem, instruction, solution, tests, config, self.source, self.runner.image)
        packaged = selfcheck(task, self.runner, directory / "packaged")
        write_json(directory / "provenance.json", {"source": self.source, "problem_sha256": digest(problem),
                   "task_sha256": digest(task), "verification": gate, "packaged": packaged,
                   "outer_harbor_verified": False, "near_miss_status": "not_run"})
        return {"task_dir": str(task), "fail_to_pass": gate["fail_to_pass"], "pass_to_pass": gate["pass_to_pass"],
                "nop_reward": packaged["nop"], "oracle_reward": packaged["oracle"]}

    def split_mask(self, complete, problem):
        changes = changed(complete, problem)
        test_changes = {p for p in changes if owned(p, self.test_paths)}
        production = changes - test_changes
        self.check_ownership(production, "production")
        self.check_ownership(test_changes, "test")
        if any((problem / p).exists() or not (complete / p).is_file() for p in test_changes):
            raise FactoryError("Mask must delete selected test files completely; other tests remain unchanged")
        solution = diff(problem, complete, production)
        tests = diff(problem, complete, test_changes)
        return solution, tests

    def ablation(self):
        self.event("ablation")
        proposal, _ = self.roles.run("analyze", self.base, self.inputs(), readonly=True)
        result, problem = self.roles.run("ablate", self.base, self.inputs(proposal=proposal))
        solution, tests = self.split_mask(self.base, problem)
        plan = self.plan(result)
        (self.output / "ablation.patch").write_text(diff(self.base, problem))
        return self.finish("ablation", problem, result["instruction"], solution, tests, plan)

    def forward(self):
        query = None
        version, feedback, retry_role = 0, None, "query"
        test_result = solution_result = None
        for attempt in range(self.repair_rounds + 1):
            self.event("forward", attempt=attempt, retry_role=retry_role)
            try:
                if retry_role == "query":
                    version += 1
                    analysis, _ = self.roles.run("analyze", self.base, self.inputs(feedback=feedback), readonly=True)
                    query, _ = self.roles.run("query", self.base, self.inputs(analysis=analysis, version=version, feedback=feedback), readonly=True)
                    evidence = query.get("evidence_paths", [])
                    if len(set(evidence)) < 2 or any(not (self.base / relative(p)).is_file() for p in evidence):
                        raise Revision("query", "Query requires at least two real repository evidence paths")
                    if not query.get("requirements") or not query.get("instruction"):
                        raise Revision("query", "Query is missing requirements or instruction")
                    write_json(self.output / f"query-v{version}.json", query)
                    test_result = solution_result = None
                if retry_role in {"query", "testcase"}:
                    test_result, test_state = self.roles.run("testcase", self.base, self.inputs(query=query, feedback=feedback))
                    self.check_ownership(changed(self.base, test_state), "test")
                    plan = self.plan(test_result)
                    before = self.runner.suite(test_state, plan["target_command"], self.output / f"critic-execution-{attempt}")
                    if not before["healthy"] or "failed" not in before["tests"].values():
                        raise Revision("testcase", "Target tests did not produce healthy assertion failures")
                    critic, _ = self.roles.run("critic", self.base, self.inputs(query=query, test_patch=test_result["captured_patch"], report=before), readonly=True)
                    if critic.get("verdict") != "valid" or critic.get("failure_matches_query") is not True:
                        raise Revision("query" if critic.get("verdict") == "query" else "testcase", critic.get("reason", "Failure critic rejected"))
                    solution_result = None
                if solution_result is None or retry_role == "solution":
                    solution_result, state = self.roles.run("solution", self.base, self.inputs(query=query, testcase=test_result, feedback=feedback))
                    self.check_ownership(changed(self.base, state), "production")
                plan["build_command"] = command(solution_result.get("build_command", []), empty=True)
                result = self.finish(f"query-attempt-{attempt}", self.base, query["instruction"],
                                     solution_result["captured_patch"], test_result["captured_patch"], plan)
                return dict(result, query_version=version, repair_count=attempt)
            except Revision as exc:
                feedback, retry_role = str(exc), exc.role
                self.event("revision_requested", role=retry_role, reason=feedback)
        raise FactoryError(f"Shared repair budget exhausted: {feedback}")

    def specialize(self):
        reference = Path(self.candidate["reference_dir"])
        task_id = self.candidate["reference_task_id"]
        entries = read_json(reference / "manifest.json")["tasks"]
        matches = [r for r in entries if r.get("task_id") == task_id and r.get("ext_id")]
        if len(matches) != 1:
            raise FactoryError("Reference manifest requires unique task_id and ext_id")
        task = reference / relative(task_id)
        required = ["task.toml", "instruction.md", "solution/solution.patch", "tests/test.patch", "tests/config.json",
                    "tests/test.sh", "tests/grader.py", "environment/Dockerfile", "tests/Dockerfile"]
        if any(not (task / p).is_file() for p in required):
            raise FactoryError("Incomplete specialization reference task")
        import tomllib
        metadata = tomllib.loads((task / "task.toml").read_text()).get("metadata", {})
        reference_config = read_json(task / "tests/config.json")
        if (metadata.get("base_commit") or reference_config.get("base_commit")) != self.source["commit"]:
            raise FactoryError("Reference base commit does not match candidate")
        reference_data = {p: (task / p).read_text() for p in required if p.endswith((".patch", ".md", ".json"))}
        understanding, _ = self.roles.run("analyze", self.base, self.inputs(reference=reference_data), readonly=True)
        full = copy_tree(self.base, self.output / "reference-full")
        self.check_ownership(apply(full, reference_data["solution/solution.patch"]), "production")
        self.check_ownership(apply(full, reference_data["tests/test.patch"]), "test")
        extra, augmented = self.roles.run("augment", full, self.inputs(reference=reference_data, understanding=understanding))
        self.check_ownership(changed(full, augmented), "test")
        if any((full / p).exists() for p in changed(full, augmented)):
            raise FactoryError("Reference augmentation must add separate test files and preserve existing tests")
        mask, base_problem = self.roles.run("mask", augmented, self.inputs(purpose="base", augmentation=extra))
        solution, tests = self.split_mask(augmented, base_problem)
        base_result = self.finish("base", base_problem, mask["instruction"], solution, tests, self.plan(mask))
        scaffold = copy_tree(self.base, self.output / "reference-scaffold")
        self.check_ownership(apply(scaffold, reference_data["tests/test.patch"]), "test")
        plan, _ = self.roles.run("plan", scaffold, self.inputs(reference=reference_data, understanding=understanding), readonly=True)
        for key in ("surface_gap", "transferred_mechanisms", "requirements", "instruction"):
            if not plan.get(key):
                raise FactoryError(f"Specialization plan missing {key}")
        evolved, full_new = self.roles.run("evolve", scaffold, self.inputs(plan=plan))
        modifications = changed(scaffold, full_new)
        if any(not owned(p, self.test_paths + self.production_paths) for p in modifications):
            raise FactoryError("Evolution changed unowned paths")
        if any(owned(p, self.test_paths) and (scaffold / p).exists() for p in modifications):
            raise FactoryError("Evolution may add tests but may not weaken/modify reference tests")
        mask, problem = self.roles.run("mask", full_new, self.inputs(purpose="specialized", plan=plan, evolution=evolved))
        solution, tests = self.split_mask(full_new, problem)
        rewritten, _ = self.roles.run("rewrite", problem, self.inputs(instruction=mask["instruction"], requirements=plan["requirements"]), readonly=True)
        if rewritten.get("requirements") != plan["requirements"]:
            raise FactoryError("Rewrite changed the structured requirement contract")
        result = self.finish("specialized", problem, rewritten["instruction"], solution, tests, self.plan(mask))
        return dict(result, base=base_result, rewrite_semantic_review="not_independently_verified")
