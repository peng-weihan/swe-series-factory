"""Command-line entry points for deterministic preparation and real construction."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import FactoryError, read_json, write_json


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="nl2repo", description="Build NL2Repo instances following nl2repo.md")
    commands = result.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Extract AST and test-import evidence without importing repository code")
    scan.add_argument("repo", type=Path)
    scan.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("check", help="Validate a generated specification against its AST evidence")
    check.add_argument("--start", type=Path, required=True)
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--ast", type=Path, required=True)
    check.add_argument("--output", type=Path)
    score = commands.add_parser("score", help="Apply the documented text-scoring protocol")
    score.add_argument("log", type=Path)
    score.add_argument("--total", type=int, required=True)
    score.add_argument("--test-file", action="append", default=[])
    demo = commands.add_parser("demo", help="Run real TagKit correct/empty/broken tests locally; no Docker or model")
    demo.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run", help="Construct instances using configured tool Agent and Docker")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--repo-jsonl", type=Path, required=True)
    run.add_argument("--start-index", type=int, default=0)
    run.add_argument("--end-index", type=int, default=0)
    run.add_argument("--reverse", action="store_true")
    run.add_argument("--limit", type=int, default=1)
    recover = commands.add_parser("recover", help="Complete or roll back a journaled repair after interruption")
    recover.add_argument("--instance", type=Path, required=True)
    recover.add_argument("--image", type=Path, required=True)
    recover.add_argument("--journal", type=Path, required=True)
    recover.add_argument("--action", choices=("commit", "rollback"), required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        exit_code = 0
        if args.command == "scan":
            from .ast_scan import scan_repository
            result = scan_repository(args.repo)
            write_json(args.output, result)
            result = {"output": str(args.output), "summary": result["summary"]}
        elif args.command == "check":
            from .specification import validate_specification
            result = validate_specification(args.start.read_text(), read_json(args.manifest), read_json(args.ast))
            if args.output:
                write_json(args.output, result)
            exit_code = 0 if result["passed"] else 1
        elif args.command == "score":
            from .verifier import score_output
            result = score_output(args.log.read_text(), args.total, set(args.test_file))
        elif args.command == "demo":
            from .demo import run_demo
            result = run_demo(args.output)
        elif args.command == "run":
            from .config import Config
            from .pipeline import Pipeline
            from .source import load_records
            records = load_records(args.repo_jsonl, args.start_index, args.end_index, args.reverse, args.limit)
            for record in records:
                if record.get("local_path"):
                    local = Path(record["local_path"]).expanduser()
                    record["local_path"] = str((args.repo_jsonl.resolve().parent / local).resolve()) if not local.is_absolute() else str(local)
            if not records:
                raise FactoryError("No selected candidate repositories")
            result = Pipeline(Config.load(args.config)).run(records)
            exit_code = 0 if all(item["status"] in {"accepted", "filtered", "built_unverified"} for item in result["results"]) else 1
        else:
            from .repair import RepairTransaction
            if not args.journal.is_file():
                raise FactoryError("Recovery requires an existing transaction journal")
            transaction = RepairTransaction(args.instance, args.image, args.journal)
            getattr(transaction, args.action)()
            result = {"state": transaction.state, "journal": str(args.journal)}
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return exit_code
    except (FactoryError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
