Construct an existing-test ablation from the complete input snapshot. Remove/stub
one selected production behavior while preserving imports, public types and test
collection. Delete selected hidden test files completely; leave regression tests
unchanged and available. Modify only declared production_paths and test_paths.
Do not create an answer patch: the controller derives the inverse from snapshots.
Return JSON with status=ok, instruction (self-contained behavior, interfaces, errors,
boundaries, compatibility and non-goals), target_command, regression_command and
build_command (empty if inapplicable). Commands are argv arrays, never shell -c.
Use {python} for the configured Python; non-pytest runners must write JUnit at {report}.
Edit workspace OR return a binary-safe patch, not both. Do not commit or change .git.
