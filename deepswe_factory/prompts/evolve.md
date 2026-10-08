Implement the specialization plan on the original problem plus reference tests.
Modify only production_paths; ADD new acceptance files within test_paths, preserving
every existing reference/regression test unchanged. Ensure the resulting complete
state implements all selected contracts. Return status=ok, implemented_behaviors,
test_paths, target_command, regression_command, build_command, known_limits.
Do not apply gold blindly, modify .git, commit, or claim tests ran without evidence.
