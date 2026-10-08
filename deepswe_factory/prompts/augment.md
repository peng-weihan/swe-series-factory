The reference solution and tests are already applied. Add boundary tests that expose
the reference difficulty mechanism. Only change test_paths; preserve all existing
assertions. New tests must actually be selected by the native runner. Return
status=ok, summary, added_test_node_ids, target_command, regression_command,
test_paths and coverage reasons. Do not edit production code or commit.
