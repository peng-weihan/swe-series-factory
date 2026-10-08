Construct a black-box behavior reconstruction instance for the fixed candidate.
The workspace contains repo/ at the controller-verified full commit. Never change
reference source files. Inspect README/build/CI and select a real offline CLI target.
Build and probe the real executable; expected values must come from observations.
Return status=rejected with reason for non-CLI, network-only, unresolved license or
unsuitable cost. Do not invent success or overwrite controller evidence.

Deliver these files within this workspace:
- build.sh: reproducible build starting from repo/, writes ./executable, executable bit set.
- instance/Dockerfile: independent build, full source commit and digest-pinned base
  images, recorded toolchain/dependency versions, /workspace/executable and
  /opt/programbench-stashed-executable-do-not-modify. Save the actual build checkout's
  git rev-parse HEAD to /opt/programbench-source-commit. Include Python+pytest for R2
  replay, declare runtime libraries. Keep source/build caches and Git history out of
  final stage. Use an explicit numeric non-root USER. Both gold files must be owned
  by root with mode 111 (execute-only); evaluator executes separately as container
  root. This permission policy is only the R2 check, not proof of R3 anti-copy safety.
- instance/eval/branches/<area>/tests/test_<area>.py: self-contained top-level sync
  test functions, no decorators, classes, conftest or cross-module dependencies.
  Use ./executable with absolute path captured before fixture cwd changes.
  Assert real bytes, exits and file effects; no gold calls during tests or skips.

Aim for 5-10 branches and 100-400 meaningful tests, covering core operations, I/O,
parameters, boundaries and errors. These are generation goals; controller QC needs
at least 8 effective tests. Never generate final tests.json/run.sh/statistics yourself.

Return a JSON object with status=ok and:
eligible=true, license_file (path within repo), license_reason, tool_type, summary,
build_command (argv, typically ["bash","build.sh"]), build_assets (additional paths
needed beside repo and build.sh), public_files (paths within repo), source_globs,
core_branches (descriptive names), probe_cases and test_observations.
probe_cases: [{"id":"help","args":["--help"]}, {"id":"core-case","args":[],
"stdin_base64":"...","input_files":{"relative-name":"base64"}}]. Include normal,
boundary and error cases. Controller repeats each experiment with byte capture.
test_observations maps "core/tests/test_core.py::test_name" to ["core-case"], for
every test. Files and stdout/stderr expectations must trace to observed behavior.
Do not commit/change .git. Your CLI must provide its own task-scoped tool sandbox;
controller path/diff checks do not sandbox arbitrary shell actions.
