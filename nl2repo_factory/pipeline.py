"""Single-repository orchestration with explicit stage records and bounded retries."""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

from .agents import Agent, AgentResponseError, CommandAgent
from .ast_scan import scan_repository
from .common import FactoryError, contained, read_json, require_process, run_process, sha256, write_json
from .config import Config
from .contracts import is_infrastructure_error, validate_a1
from .docker import DockerAdapter, image_name
from .oracle import DockerOracle
from .packaging import materialize_instance
from .repair import RepairTransaction, validate_repair
from .source import task_id as make_task_id
from .specification import clean_start_md, validate_specification


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def make_prompt(role: str, inputs: dict) -> str:
    template = (Path(__file__).parent / "prompts" / f"{role}.md").read_text()
    return template + "\n\n## Current invocation\n\n```json\n" + json.dumps(inputs, ensure_ascii=False, indent=2) + "\n```\n"


def acquire_repository(record: dict, destination: Path) -> dict:
    """Use a fresh task-private snapshot, retaining the observed commit in a ledger."""
    if destination.exists():
        raise FactoryError(f"Acquisition destination already exists: {destination}")
    if record.get("local_path"):
        source = Path(record["local_path"]).expanduser().resolve()
        if not source.is_dir() or destination.resolve().is_relative_to(source):
            raise FactoryError("local_path must be a directory outside the task output")
        for root, directories, files in os.walk(source, followlinks=False):
            directories[:] = [x for x in directories if x not in {".venv", "__pycache__"}]
            for name in directories + files:
                path = Path(root) / name
                if path.is_symlink() and not path.resolve().is_relative_to(source):
                    raise FactoryError(f"Repository symlink escapes source: {path}")
        is_git = (source / ".git").exists()
        if is_git and record.get("commit"):
            # A fresh clone also handles worktree .git indirections without touching the source.
            require_process(run_process(["git", "clone", "--no-hardlinks", "--", str(source), str(destination)], timeout=900), "Local repository clone")
        else:
            if record.get("commit"):
                raise FactoryError("commit cannot be used with a non-Git local snapshot")
            observed = None
            if is_git:
                head = run_process(["git", "rev-parse", "HEAD"], cwd=source, timeout=30)
                observed = head.stdout.strip() if head.returncode == 0 else None
            shutil.copytree(source, destination, symlinks=True, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
            return {"url": record["url"], "repo_path": str(destination), "commit": observed,
                    "requested_commit": None, "local_snapshot": True,
                    "snapshot_note": "working tree including uncommitted content; observed HEAD is provenance only"}
    else:
        require_process(run_process(["git", "clone", "--", record["url"], str(destination)], timeout=900), "Repository clone")
    if record.get("commit"):
        commit = record["commit"]
        if not isinstance(commit, str) or commit.startswith("-"):
            raise FactoryError("commit must be a revision string, not a git option")
        require_process(run_process(["git", "checkout", "--detach", commit], cwd=destination, timeout=120), "Commit checkout")
    result = run_process(["git", "rev-parse", "HEAD"], cwd=destination, timeout=30)
    return {"url": record["url"], "repo_path": str(destination),
            "commit": result.stdout.strip() if result.returncode == 0 else None,
            "requested_commit": record.get("commit"), "local_snapshot": bool(record.get("local_path"))}


class Pipeline:
    def __init__(self, config: Config, *, agent: Agent | None = None, docker=None, oracle=None):
        self.config = config
        self.docker = docker or DockerAdapter(config.docker_binary, config.docker_host, config.build_timeout)
        env = {"DOCKER_HOST": config.docker_host} if config.docker_host else {}
        self.agent = agent or CommandAgent(config.agent_argv, env=env, stdin_prompt=config.agent_stdin)
        self.oracle = oracle or DockerOracle(self.docker, config.oracle_timeout)
        self.run_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.run_dir = config.root / self.run_id
        self._active_stages: dict[str, str] = {}

    def event(self, work: Path, stage: str, status: str, **details) -> dict:
        self._active_stages[work.name] = stage
        event = {"schema_version": "nl2repo.stage_record.v1", "task_id": work.name,
                 "time": utcnow(), "stage": stage, "status": status, **details}
        write_json(work / f"{stage}.json", event)
        for path in (work / "stage_events.jsonl", self.run_dir / "pipeline_events.jsonl"):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as output:
                output.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        return event

    def run(self, records: list[dict]) -> dict:
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.config.dataset_root.mkdir(parents=True, exist_ok=True)
        self.config.image_root.mkdir(parents=True, exist_ok=True)
        write_json(self.run_dir / "inputs.json", records)
        preflight = self.docker.preflight()
        write_json(self.run_dir / "runtime.json", {"python": sys.version, "docker": preflight,
                                                    "implementation": "nl2repo-factory/0.1.0", "started_at": utcnow()})
        results = []
        for index, record in enumerate(records):
            task = record.get("task_id") or make_task_id(record["url"])
            work = self.run_dir / "work" / task
            if work.exists():
                # Keep input duplicates, but never race/overwrite a previously processed identity.
                results.append({"task_id": task, "status": "identity_conflict", "input_index": index})
            else:
                work.mkdir(parents=True)
                try:
                    results.append(self.run_one(record, work))
                except Exception as exc:
                    stage = self._active_stages.get(task, "input")
                    status = "infrastructure_failed" if is_infrastructure_error(str(exc)) else {
                        "input": "acquisition_failed", "agent1": "agent1_failed", "ast": "ast_failed",
                        "agent2": "specification_failed", "packaging": "packaging_failed",
                        "oracle": "oracle_failed", "repair": "repair_failed",
                    }.get(stage, "failed")
                    self.event(work, "terminal", status, error=str(exc), exception_type=type(exc).__name__)
                    results.append({"task_id": task, "status": status, "stage": stage, "error": str(exc), "work_dir": str(work)})
            write_json(self.run_dir / "checkpoint.json", {"completed": index + 1, "total": len(records), "results": results,
                                                          "automatic_resume": False})
        counts = {status: sum(r["status"] == status for r in results) for status in sorted({r["status"] for r in results})}
        summary = {"run_id": self.run_id, "run_dir": str(self.run_dir), "counts": counts, "results": results}
        write_json(self.run_dir / "summary.json", summary)
        return summary

    def _a1(self, work: Path, cycle: Path, inputs: dict) -> dict:
        for attempt in range(self.config.agent1_retries + 1):
            self.event(work, "agent1", "running", attempt=attempt, cycle=cycle.name)
            try:
                result = self.agent.run("agent1", make_prompt("agent1", inputs), inputs, cycle,
                                        cycle / "agent1_attempts" / str(attempt), self.config.agent_timeout)
            except (FactoryError, ValueError) as exc:
                self.event(work, "agent1", "call_failed", attempt=attempt, error=str(exc))
                if attempt == self.config.agent1_retries:
                    raise
                continue
            report = validate_a1(result)
            self.event(work, "agent1", "passed" if report["passed"] else "failed", result=result, validation=report)
            if not report["passed"]:
                raise FactoryError(f"A1 contract failed: {report['errors']}")
            return result
        raise AssertionError("unreachable A1 loop")

    def _a2(self, work: Path, cycle: Path, inputs: dict, payload: dict) -> tuple[dict, Path, Path]:
        feedback = None
        stage_dir = cycle / "stage3"
        stage_dir.mkdir()
        for attempt in range(self.config.coverage_retries + 1):
            current = dict(inputs, coverage_feedback=feedback, attempt=attempt,
                           start_md_path=str(stage_dir / "start.md"), api_manifest_path=str(stage_dir / "api_manifest.json"))
            self.event(work, "agent2", "running", attempt=attempt, cycle=cycle.name)
            try:
                try:
                    result = self.agent.run("agent2", make_prompt("agent2", current), current, cycle,
                                            cycle / "agent2_attempts" / str(attempt), self.config.specification_timeout)
                except AgentResponseError:
                    if not all((stage_dir / name).is_file() and (stage_dir / name).stat().st_size for name in ("start.md", "api_manifest.json")):
                        raise
                    result = {"status": "stage3_passed", "start_md_path": "stage3/start.md",
                              "api_manifest_path": "stage3/api_manifest.json", "response_recovered": True}
                if result.get("status") != "stage3_passed":
                    raise FactoryError("A2 did not declare stage3_passed")
                paths = []
                for key, default in (("start_md_path", "stage3/start.md"), ("api_manifest_path", "stage3/api_manifest.json")):
                    path = Path(result.get(key, default))
                    path = contained(path if path.is_absolute() else cycle / path, cycle)
                    if not path.is_file() or not path.stat().st_size:
                        raise FactoryError(f"Missing generated artifact: {path}")
                    paths.append(path)
                start, manifest_path = paths
                original = start.read_text()
                cleaned = clean_start_md(original)
                manifest = read_json(manifest_path)
                report = validate_specification(cleaned, manifest, payload, node_count=result.get("node_count"))
                write_json(cycle / f"coverage-{attempt}.json", report)
                if not report["passed"]:
                    feedback = report
                    self.event(work, "agent2", "coverage_failed", attempt=attempt, validation=report)
                    continue
                # Explicit engineering correction: finalize the exact same bytes before building.
                (stage_dir / "start.before-cleaning.md").write_text(original)
                start.write_text(cleaned)
                self.event(work, "agent2", "passed", attempt=attempt, validation=report,
                           start_md_sha256=sha256(start), cleaned=cleaned != original)
                return result, start, manifest_path
            except (FactoryError, ValueError, OSError, TypeError) as exc:
                feedback = {"errors": [str(exc)]}
                self.event(work, "agent2", "failed", attempt=attempt, error=str(exc))
        raise FactoryError(f"A2 exhausted retries: {feedback}")

    def _oracle(self, work: Path, instance: Path, archive: Path, label: str, *, mode="oracle") -> dict:
        result = {}
        for attempt in range(self.config.oracle_infra_retries + 1):
            job = work / "oracle_jobs" / f"{label}-{attempt}"
            result = self.oracle.run(instance, archive, job, mode=mode)
            self.event(work, "oracle" if mode == "oracle" else "empty_control", result["status"],
                       label=label, attempt=attempt, result=result)
            if result["status"] != "infrastructure_failed":
                break
        return result

    def run_one(self, record: dict, work: Path) -> dict:
        task = work.name
        destination = self.config.dataset_root / task
        archive = self.config.image_root / (task + ".tar")
        if destination.exists() or archive.exists():
            raise FactoryError(f"Task/image identity already exists; use a fresh dataset/images root: {task}")
        self.event(work, "input", "selected", record=record)
        leak_feedback = None
        for leak_round in range(self.config.image_leak_retries + 1):
            cycle = work / f"cycle-{leak_round}"
            cycle.mkdir()
            repo = cycle / "repo"
            provenance = acquire_repository(record, repo)
            write_json(cycle / "source.json", provenance)
            inputs = {"repo_path": str(repo), "task_dir": str(cycle), "task_id": task,
                      "image_name": "teamfactory-agent1-" + task.lower(),
                      "docker_binary": self.config.docker_binary, "leak_feedback": leak_feedback,
                      "repo_url": record["url"]}
            a1 = self._a1(work, cycle, inputs)
            a1["repo_path"] = str(repo)
            self.event(work, "ast", "running", cycle=cycle.name)
            payload = scan_repository(repo)
            ast_path = cycle / "ast.json"
            write_json(ast_path, payload)
            count = payload["summary"]["implementation_python_file_count"]
            self.event(work, "ast", "passed" if count >= 5 else "filtered", summary=payload["summary"], payload_path=str(ast_path))
            if count < 5:
                return {"task_id": task, "status": "filtered", "reason": "implementation_python_file_count_lt_5"}
            a2_inputs = {"repo_path": str(repo), "ast_path": str(ast_path), "a1": a1,
                         "required_api_symbols": payload["required_api_symbols"],
                         "api_budget": min(60, max(20, len(payload["public_classes"]) + len(payload["public_functions"]))) }
            specification, start, manifest = self._a2(work, cycle, a2_inputs, payload)
            staged = cycle / "instance"
            self.event(work, "packaging", "running", cycle=cycle.name)
            assets = materialize_instance(repo, staged, start, manifest, a1, payload, archive,
                                          specification.get("project_name", task))
            tag = image_name(task)
            image_metadata = self.docker.build(staged / "environment", tag, cycle / "image_build")
            leak = self.docker.leak_check(tag, cycle / "image_check", sha256(start))
            self.event(work, "packaging", "passed" if leak["passed"] else "leak_failed", assets=assets, leak_check=leak)
            if not leak["passed"]:
                leak_feedback = leak
                if leak_round == self.config.image_leak_retries:
                    return {"task_id": task, "status": "leak_failed", "report": leak}
                continue
            staged_image = cycle / "image.tar"
            self.docker.save(tag, staged_image)
            write_json(staged / "build_metadata.json", {
                "schema_version": "nl2repo.build.v1", "task_id": task, "image_tag": tag,
                "source": provenance, "image": image_metadata,
                "start_md_sha256": sha256(start), "image_archive_sha256": sha256(staged_image),
                "prompt_sha256": {name: sha256(Path(__file__).parent / "prompts" / f"{name}.md")
                                  for name in ("agent1", "agent2", "repair")},
                "asset_warnings": assets.get("asset_warnings", []), "accepted": False,
                "compatibility_profile": "documented-baseline-with-explicit-engineering-corrections",
            })
            # No overwrite of an existing valid task. Cross-resource repair swaps are journaled separately.
            shutil.copytree(staged, destination, symlinks=True)
            try:
                shutil.copy2(staged_image, archive)
            except Exception:
                shutil.rmtree(destination)
                raise
            if self.config.skip_oracle:
                self.event(work, "terminal", "built_unverified")
                return {"task_id": task, "status": "built_unverified", "instance_dir": str(destination)}
            result = self._oracle(work, destination, archive, "initial")
            if result["status"] == "infrastructure_failed":
                return {"task_id": task, "status": "infrastructure_failed", "oracle": result}
            if result["status"] != "passed":
                result = self._repair(work, destination, archive, result)
            if result["status"] == "infrastructure_failed":
                return {"task_id": task, "status": "infrastructure_failed", "oracle": result}
            if result["status"] != "passed":
                return {"task_id": task, "status": "oracle_failed", "oracle": result}
            if self.config.verify_empty:
                control = self._oracle(work, destination, archive, "empty", mode="empty")
                if control["status"] in {"infrastructure_failed", "timeout", "missing_reward"} or control.get("reward") is None:
                    return {"task_id": task, "status": "empty_control_failed", "control": control}
                if control["reward"] >= 0.999999:
                    return {"task_id": task, "status": "empty_answer_passed", "control": control}
            metadata_path = destination / "build_metadata.json"
            metadata = read_json(metadata_path)
            metadata.update(accepted=True, accepted_at=utcnow(), image_archive_sha256=sha256(archive),
                            oracle_reward=result["reward"], empty_control_run=self.config.verify_empty)
            write_json(metadata_path, metadata)
            self.event(work, "terminal", "accepted", instance_dir=str(destination), reward=result["reward"])
            return {"task_id": task, "status": "accepted", "instance_dir": str(destination), "reward": result["reward"]}
        raise AssertionError("unreachable leak loop")

    def _repair(self, work: Path, instance: Path, archive: Path, result: dict) -> dict:
        canonical_result = result
        rejected_feedback = None
        for round_number in range(max(1, self.config.repair_rounds)):
            repair_dir = work / "repairs" / str(round_number)
            candidate = repair_dir / "candidate"
            shutil.copytree(instance, candidate, symlinks=True)
            evidence = repair_dir / "evidence"
            evidence.mkdir()
            write_json(evidence / "oracle_result.json", result)
            trial = result.get("trial_dir")
            if trial and Path(trial).is_dir():
                shutil.copytree(trial, evidence / "trial", symlinks=True)
            (repair_dir / "FILE_INDEX.txt").write_text("\n".join(str(p.relative_to(repair_dir)) for p in sorted(repair_dir.rglob("*")) if p.is_file()) + "\n")
            inputs = {"candidate_dir": str(candidate), "evidence_dir": str(evidence),
                      "file_index": str(repair_dir / "FILE_INDEX.txt"), "round": round_number,
                      "oracle_result": canonical_result, "rejected_candidate_feedback": rejected_feedback}
            try:
                response = self.agent.run("repair", make_prompt("repair", inputs), inputs, repair_dir,
                                          repair_dir / "agent_attempt", self.config.repair_timeout)
            except (FactoryError, ValueError, OSError) as exc:
                rejected_feedback = {"status": "agent_call_failed", "error": str(exc)}
                self.event(work, "repair", "agent_call_failed", round=round_number, error=str(exc))
                continue
            validation = validate_repair(instance, candidate, response, canonical_result.get("report"))
            self.event(work, "repair", "validated" if validation["passed"] else "rejected",
                       round=round_number, response=response, validation=validation)
            if not validation["passed"]:
                rejected_feedback = {"status": "validation_failed", "validation": validation}
                continue
            if response["status"] == "unrepairable":
                return result
            metadata = read_json(instance / "build_metadata.json")
            tag = metadata["image_tag"]
            context = repair_dir / "image_context"
            context.mkdir()
            shutil.copy2(candidate / "environment/start.md", context / "start.md")
            commands = response.get("image_commands", [])
            (context / "Dockerfile").write_text("FROM " + tag + "\nUSER root\n" +
                "".join("RUN " + command + "\n" for command in commands) +
                "COPY start.md /workspace/start.md\nWORKDIR /workspace\n")
            candidate_tag = tag + "-repair-" + str(round_number)
            try:
                self.docker.load(archive)
                image_info = self.docker.build(context, candidate_tag, repair_dir / "build")
                leak = self.docker.leak_check(candidate_tag, repair_dir / "leak", sha256(context / "start.md"))
            except (FactoryError, OSError) as exc:
                rejected_feedback = {"status": "image_build_failed", "error": str(exc)}
                self.event(work, "repair", "image_build_failed", round=round_number, error=str(exc))
                if is_infrastructure_error(str(exc)):
                    return {"status": "infrastructure_failed", "reward": None, "error": str(exc)}
                continue
            if not leak["passed"]:
                rejected_feedback = {"status": "image_leak_failed", "leak": leak}
                self.event(work, "repair", "leak_failed", round=round_number, leak=leak)
                continue
            candidate_archive = repair_dir / "candidate.tar"
            self.docker.save(candidate_tag, candidate_archive)
            # Controller-owned metadata is updated only after validating the Agent's changes.
            metadata.update(image_tag=candidate_tag, image=image_info, image_archive_sha256=sha256(candidate_archive))
            write_json(candidate / "build_metadata.json", metadata)
            transaction = RepairTransaction(instance, archive, repair_dir / "transaction.json")
            transaction.switch(candidate, candidate_archive)
            try:
                checked = self._oracle(work, instance, archive, f"repair-{round_number}")
            except BaseException:
                transaction.rollback()
                raise
            if checked["status"] == "passed":
                # A failed commit is recovered from its journal; never start a conflicting rollback.
                transaction.commit()
                return checked
            transaction.rollback()
            # Keep failure evidence, but validate subsequent patches against restored canonical content.
            rejected_feedback = checked
            if checked["status"] == "infrastructure_failed":
                return checked
        return canonical_result
