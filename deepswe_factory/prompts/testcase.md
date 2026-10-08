Author tests against the unchanged base and supplied query. Change only declared
test_paths, never production code. Assertions must fail on old behavior for the
requested reason, not missing dependencies, collection errors or timeouts. Preserve
native regression tests. Return status=ok, summary, requirement_test_matrix,
target_command, regression_command, build_command=[], and changed test_paths.
Commands must be argv arrays. Use {python}; pytest gets JUnit added automatically,
other runners must explicitly write JUnit to {report}. Test IDs must come from real
reports. Edit files OR return patch; do not commit. Include new files in the result.
