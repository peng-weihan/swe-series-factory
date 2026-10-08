from __future__ import annotations

import base64
import json
import re
import shutil
from pathlib import Path

from nl2repo_factory.common import FactoryError, require_process, sha256, write_json
from swe_factory.roles import Roles
from swe_factory.runtime import Runner, command
from swe_factory.snapshots import acquire, copy_tree, digest, files, relative
from .quality import classify, difficulty, flatten
from .delivery import check_recipe, inspect_cleanroom


class Pipeline:
    def __init__(self, runner, agent=None, *, min_kept=8, agent_timeout=1800, rebuild=True):
        if min_kept < 8:
            raise FactoryError("ProgramBench requires at least eight effective tests")
        self.runner, self.agent, self.min_kept = runner, agent, min_kept
        self.agent_timeout, self.rebuild = agent_timeout, rebuild

    def event(self, stage, **details):
        self.stage = stage
        self.events.append({"stage": stage, **details})
        write_json(self.output / "stages.json", self.events)

    def run(self, candidate: dict, output: Path):
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.events, self.stage = [], "input"
        write_json(self.output / "input.json", candidate)
        try:
            for key in ("org", "repo"):
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", candidate[key]) or candidate[key] in {".", ".."}:
                    raise FactoryError(f"Unsafe candidate {key}")
            identity = candidate["org"] + "__" + candidate["repo"] + "." + candidate["commit"][:7]
            self.event("acquire")
            author_input = self.output / "author-input"
            source = acquire(candidate, author_input / "repo")
            original = files(author_input / "repo")
            roles = Roles(self.agent, self.output / "roles", Path(__file__).with_name("prompts"), self.agent_timeout)
            self.event("generate")
            manifest, generated = roles.run("builder", author_input, {"candidate": candidate, "source": source})
            generated_files = files(generated / "repo")
            for name, entry in original.items():
                if generated_files.get(name) != entry:
                    raise FactoryError("Builder changed the frozen reference source")
            if manifest.get("eligible") is not True:
                raise FactoryError("Builder did not confirm offline CLI eligibility")
            license_file = generated / "repo" / relative(manifest["license_file"])
            if not license_file.is_file() or not manifest.get("license_reason"):
                raise FactoryError("License evidence is required")
            recipe = generated / "instance/Dockerfile"
            branches_dir = generated / "instance/eval/branches"
            if not recipe.is_file() or not branches_dir.is_dir() or not (generated / "build.sh").is_file():
                raise FactoryError("Missing build script, Dockerfile or test branches")
            # Controller rebuilds from frozen inputs, excluding agent scratch artifacts.
            build_input = self.output / "build-input"
            copy_tree(author_input / "repo", build_input / "repo")
            shutil.copy2(generated / "build.sh", build_input / "build.sh")
            for path in manifest.get("build_assets", []):
                name = relative(path)
                if name == "build.sh" or name.startswith("repo/"):
                    raise FactoryError("Build assets may not overwrite frozen source or build.sh")
                asset = generated / name
                if not asset.is_file():
                    raise FactoryError("Missing declared build asset")
                target = build_input / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(asset, target)
            build_command = command(manifest.get("build_command", ["bash", "build.sh"]))
            self.event("build")
            build = self.runner.run(build_input, build_command, self.output / "build")
            require_process(build["execution"], "Reference build")
            gold = build["workspace"] / "executable"
            if not gold.is_file() or not gold.stat().st_mode & 0o111:
                raise FactoryError("Build must produce an executable file named executable")
            write_json(self.output / "build_manifest.json", {"source": source, "command": build_command,
                       "binary_sha256": sha256(gold), "runtime": build["runtime"], "build_script_sha256": sha256(build_input / "build.sh"),
                       "license": {"path": manifest["license_file"], "sha256": sha256(license_file), "reason": manifest["license_reason"]}})
            self.event("probe")
            observations = self.probe(gold, manifest["probe_cases"])
            proposed = generated / "instance"
            task = self.output / "instances" / identity
            task.mkdir(parents=True, exist_ok=False)
            shutil.copy2(recipe, task / "Dockerfile")
            branches = flatten(branches_dir, task / "eval/tests", candidate["commit"])
            write_json(self.output / "test_mapping.json", branches)
            self.event("quality_control")
            reports = self.qc(task, gold, self.runner, self.output / "qc")
            quality = classify(branches, reports["dummy"], reports["gold"], min_kept=self.min_kept)
            if not quality["passed"]:
                raise FactoryError(f"Too few effective tests: {quality['n_kept']} < {self.min_kept}")
            active_names = {b["name"] for b in branches.values() if not b["ignored"]}
            if not active_names.intersection(manifest.get("core_branches", [])):
                raise FactoryError("No effective core-function branch; help/version alone is insufficient")
            probe_ids = {p["probe_id"] for p in observations}
            provenance = manifest.get("test_observations", {})
            for branch in branches.values():
                for dotted, native in branch["mapping"].items():
                    if native in quality["kept_native_ids"]:
                        links = provenance.get(branch["origins"][dotted], [])
                        if not links or not set(links) <= probe_ids:
                            raise FactoryError(f"Effective test lacks probe provenance: {dotted}")
            write_json(self.output / "test_provenance.json", provenance)
            # Repeat gold in a fresh workspace, independently of dummy and first gold.
            repeated = self.qc_case(task, gold, self.runner, self.output / "qc/gold-repeat")
            if not repeated["healthy"] or any(repeated["tests"].get(k) != "passed" for k in quality["kept_native_ids"]):
                raise FactoryError("Effective gold tests are unstable")
            help_obs = next(o for o in observations if o["probe_id"] == "help")
            label = difficulty(base64.b64decode(help_obs["stdout_base64"]).decode(errors="replace"), len(branches), quality["n_kept"])
            public = set(manifest.get("public_files", [])) | {manifest["license_file"]}
            for name in public:
                relative(name)
                if name not in original:
                    raise FactoryError("Public file not found in fixed source")
            cleaning = {p: v["sha256"] for p, v in original.items() if p not in public}
            if not cleaning:
                raise FactoryError("Source cleaning hashes cannot be empty")
            write_json(self.output / "source_cleaning_manifest.json", cleaning)
            globs = manifest.get("source_globs", [])
            if not globs:
                raise FactoryError("Explicit source_globs required")
            for pattern in globs:
                relative(pattern)
            language = {"rust": "rs"}.get(candidate.get("language"), candidate.get("language", "other"))
            if language not in {"rs", "go", "c", "cpp", "other"}:
                language = "other"
            metadata = {"repository": candidate["org"] + "/" + candidate["repo"], "commit": source["commit"],
                        "language": language, "source_globs": globs, "eval_clean_hashes": list(cleaning.values())}
            if label["difficulty"] != "unrated":
                metadata["difficulty"] = label["difficulty"]
            # JSON is valid YAML 1.2; avoids a runtime dependency for emission.
            (task / "task.yaml").write_text(json.dumps(metadata, indent=2) + "\n")
            write_json(task / "tests.json", {"branches": {bid: {k: v for k, v in b.items() if k in {"tests", "ignored", "ignore_reason", "ignored_tests"}} for bid, b in branches.items()}})
            meta = dict(category=candidate.get("category", "unknown"), language=language,
                        tool_type=manifest.get("tool_type", "CLI"), difficulty=label["difficulty"],
                        **{k: quality[k] for k in ("n_tests", "n_kept", "n_branches")},
                        build_verified=True, confidence="unreviewed", notes=manifest.get("summary", ""))
            write_json(task / "meta.json", meta)
            run_script = task / "eval/run.sh"
            run_script.write_text('#!/bin/bash\nset -e\ncd "$(dirname "$0")/.."\npython3 -m pytest eval/tests/ --junitxml=eval/results.xml -v || true\n')
            run_script.chmod(0o755)
            write_json(self.output / "quality.json", quality)
            result = {"status": "qc_passed", "level": "R1", "instance_dir": str(task), "quality": quality,
                      "source": source, "model_called": self.agent is not None and not getattr(self.agent, "synthetic", False),
                      "execution": self.runner.backend, "r2_verified": False, "r3_verified": False,
                      "published": False, "instance_sha256": digest(task)}
            if self.rebuild and self.runner.backend == "docker":
                self.event("independent_rebuild")
                result["delivery"] = self.delivery(task, candidate, quality, self.output / "delivery", set(cleaning.values()))
                staging = self.output / "publication-staging"
                copy_tree(task, staging)
                published = self.output / "published" / identity
                published.parent.mkdir()
                staging.rename(published)
                result.update(status="published", level="R2", r2_verified=True, published=True, published_dir=str(published))
            self.event(result["status"])
        except (FactoryError, OSError, ValueError, KeyError, TypeError) as exc:
            result = {"status": "failed", "stage": self.stage, "reason": str(exc), "published": False}
            write_json(self.output / "summary.json", result)
            raise FactoryError(f"{self.stage}: {exc}; evidence: {self.output}") from exc
        write_json(self.output / "summary.json", result)
        return result

    def probe(self, gold, cases):
        if not isinstance(cases, list) or not cases or len({c["id"] for c in cases}) != len(cases):
            raise FactoryError("Probe IDs must be nonempty and unique")
        if any(not isinstance(c.get("args"), list) or not all(isinstance(a, str) for a in c["args"]) for c in cases):
            raise FactoryError("Probe args must be arrays of strings")
        if not any(c["id"] == "help" and c["args"] == ["--help"] for c in cases):
            raise FactoryError("Include a help probe with args=[--help]")
        source = self.output / "probe-input"
        source.mkdir()
        shutil.copy2(gold, source / "executable")
        shutil.copy2(Path(__file__).with_name("probe.py"), source / "probe.py")
        write_json(source / "cases.json", cases)
        run = self.runner.run(source, ["{python}", "probe.py", "cases.json"], self.output / "probe")
        require_process(run["execution"], "Reference probes")
        observations = json.loads((run["workspace"] / "observations.json").read_text())
        for index in range(0, len(observations), 2):
            a, b = observations[index:index + 2]
            if a["timed_out"] or any(a[k] != b[k] for k in ("exit_code", "stdout_base64", "stderr_base64", "output_files", "timed_out")):
                raise FactoryError(f"Unstable or timed-out reference probe: {a['probe_id']}")
        write_json(self.output / "observations.json", observations)
        return observations

    def qc_case(self, task, executable, runner, evidence):
        source = evidence.parent / (evidence.name + "-input")
        copy_tree(task / "eval/tests", source / "tests")
        shutil.copy2(executable, source / "executable")
        return runner.suite(source, ["{python}", "-m", "pytest", "tests", "-q"], evidence)

    def qc(self, task, gold, runner, evidence):
        evidence.mkdir(parents=True, exist_ok=False)
        dummy = evidence / "dummy-executable"
        dummy.write_text("#!/bin/sh\nexit 0\n")
        dummy.chmod(0o755)
        return {"dummy": self.qc_case(task, dummy, runner, evidence / "dummy"),
                "gold": self.qc_case(task, gold, runner, evidence / "gold")}

    def delivery(self, task, candidate, quality, evidence, source_hashes=None):
        recipe = (task / "Dockerfile").read_text()
        check_recipe(recipe, candidate["commit"])
        if not source_hashes:
            raise FactoryError("R2 requires nonempty source-cleaning content hashes")
        tag = "programbench-rebuild-" + digest(task)[:16]
        image = self.runner.docker.build(task, tag, evidence)
        runner = Runner(backend="docker", image=image["id"], python=self.runner.python, timeout=self.runner.timeout,
                        docker_binary=self.runner.docker.binary, docker_host=self.runner.docker.env.get("DOCKER_HOST"))
        empty = evidence / "empty-input"
        empty.mkdir(parents=True)
        script = ("import shutil,sys; from pathlib import Path; "
                  "actual=Path('/opt/programbench-source-commit').read_text().strip(); "
                  "assert actual == sys.argv[1], 'rebuilt source commit mismatch'; "
                  "shutil.copy2('/opt/programbench-stashed-executable-do-not-modify','executable'); "
                  "Path('executable').chmod(0o755)")
        export = runner.run(empty, ["{python}", "-c", script, candidate["commit"]], evidence / "export")
        require_process(export["execution"], "Export rebuilt reference")
        gold = export["workspace"] / "executable"
        checks = self.qc(task, gold, runner, evidence / "qc")
        repeated = self.qc_case(task, gold, runner, evidence / "gold-repeat")
        for key, status in (("dummy", "failed"), ("gold", "passed")):
            report = checks[key]
            if not report["healthy"] or any(report["tests"].get(k) != status for k in quality["kept_native_ids"]):
                raise FactoryError(f"Independent rebuild {key} did not satisfy effective tests")
        if not repeated["healthy"] or any(repeated["tests"].get(k) != "passed" for k in quality["kept_native_ids"]):
            raise FactoryError("Independent gold was unstable")
        import uuid
        name = "pb-cleanroom-" + uuid.uuid4().hex
        docker = runner.docker
        user = json.loads(require_process(docker.execute(["image", "inspect", image["id"], "--format", "{{json .Config.User}}"]), "Inspect solver UID").stdout)
        require_process(docker.execute(["create", "--name", name, "--entrypoint", "/bin/true", image["id"]]), "Create cleanroom inspection")
        try:
            archive = evidence / "runtime-filesystem.tar"
            require_process(docker.execute(["export", "--output", str(archive), name]), "Export runtime filesystem")
            hidden_hashes = {sha256(p) for p in (task / "eval/tests").rglob("*.py")}
            cleanroom = inspect_cleanroom(archive, source_hashes | hidden_hashes, user)
            write_json(evidence / "cleanroom.json", cleanroom)
        finally:
            docker.execute(["rm", "-f", name], timeout=30)
        if not cleanroom["passed"]:
            raise FactoryError("R2 cleanroom checks failed: " + "; ".join(cleanroom["errors"]))
        result = {"image": image, "gold_sha256": sha256(gold), "effective_tests_passed": True,
                  "r3_verified": False, "cleanroom_check": cleanroom, "r2_verified": True}
        write_json(evidence / "summary.json", result)
        return result
