from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from nl2repo_factory.common import FactoryError, write_json
from .config import load, records


def main(family: str, argv=None):
    parser = argparse.ArgumentParser(prog=family, description=f"Build {family} instances with explicit verification levels")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Execute synthetic host fixture; no model or Docker")
    demo.add_argument("--output", type=Path, required=True)
    if family == "deepswe":
        demo.add_argument("--route", choices=["all", "ablation", "query", "specialize"], default="all")
    run = commands.add_parser("run", help="Construct fixed candidates using configured agent and execution backend")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--repo-jsonl", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--limit", type=int, default=1)
    check = commands.add_parser("validate-config", help="Read config only; no model, build or Docker calls")
    check.add_argument("--config", type=Path, required=True)
    if family == "deepswe":
        verify = commands.add_parser("verify", help="Replay a frozen task's packaged Nop/Oracle; no agent calls")
        verify.add_argument("--config", type=Path, required=True)
        verify.add_argument("--task", type=Path, required=True)
        verify.add_argument("--output", type=Path, required=True)
    else:
        rebuild = commands.add_parser("rebuild", help="Independently rebuild and replay a saved R1 construction")
        rebuild.add_argument("--config", type=Path, required=True)
        rebuild.add_argument("--construction", type=Path, required=True)
        rebuild.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            if family == "deepswe":
                from deepswe_factory.demo import run_demo
                result = run_demo(args.output, args.route)
            else:
                from programbench_factory.demo import run_demo
                result = run_demo(args.output)
        elif args.command == "validate-config":
            data, runner, agent = load(args.config)
            result = {"valid": True, "backend": runner.backend, "agent_configured": agent is not None,
                      "runtime_executed": False}
        elif args.command == "verify":
            from deepswe_factory.packaging import selfcheck
            _, runner, _ = load(args.config)
            if args.output.exists():
                raise FactoryError("Verification output must be a new directory")
            result = selfcheck(args.task.resolve(), runner, args.output.resolve())
            result.update(execution=runner.backend, outer_harbor_verified=False)
        elif args.command == "rebuild":
            from nl2repo_factory.common import read_json
            from programbench_factory.pipeline import Pipeline
            _, runner, _ = load(args.config)
            if runner.backend != "docker":
                raise FactoryError("Independent R2 rebuild requires Docker")
            if args.output.exists():
                raise FactoryError("Rebuild evidence directory must be new")
            candidate = read_json(args.construction / "input.json")
            quality = read_json(args.construction / "quality.json")
            identity = candidate["org"] + "__" + candidate["repo"] + "." + candidate["commit"][:7]
            cleaning = read_json(args.construction / "source_cleaning_manifest.json")
            result = Pipeline(runner).delivery(args.construction / "instances" / identity, candidate, quality, args.output, set(cleaning.values()))
        else:
            data, runner, agent = load(args.config)
            if agent is None:
                raise FactoryError("Generation requires configured agent.argv")
            if args.limit < 1:
                raise FactoryError("limit must be positive")
            candidates = records(args.repo_jsonl)[:args.limit]
            args.output.mkdir(parents=True, exist_ok=False)
            write_json(args.output / "run_config.json", {"runtime": data.get("runtime", {}), "pipeline": data.get("pipeline", {}),
                       "agent_transport": "json_actions" if data.get("model") else "command", "model": data.get("model", {}).get("model"),
                       "credential_values_recorded": False})
            if family == "deepswe":
                from deepswe_factory.pipeline import Pipeline
                options = {"repair_rounds": data.get("pipeline", {}).get("repair_rounds", 3)}
            else:
                from programbench_factory.pipeline import Pipeline
                options = {"min_kept": data.get("pipeline", {}).get("min_kept", 8), "rebuild": data.get("pipeline", {}).get("rebuild", True)}
            results, seen = [], set()
            for index, candidate in enumerate(candidates):
                key = (str(candidate.get("url", candidate.get("local_path", ""))).lower(), candidate.get("commit"), candidate.get("route"))
                if key in seen:
                    results.append({"status": "identity_conflict", "input_index": index})
                    continue
                seen.add(key)
                try:
                    results.append(Pipeline(runner, agent, **options).run(candidate, args.output / f"candidate-{index:04d}"))
                except FactoryError as exc:
                    results.append({"status": "failed", "reason": str(exc), "input_index": index})
                write_json(args.output / "summary.json", {"results": results})
            result = {"results": results}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if any(r.get("status") in {"failed", "identity_conflict"} for r in result.get("results", [])) else 0
    except (FactoryError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2
