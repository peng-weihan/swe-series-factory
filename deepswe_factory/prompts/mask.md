The workspace contains a complete implementation. Mask the chosen mechanism and
delete selected acceptance-test files entirely, preserving API skeletons, imports,
unrelated functionality and regression tests. Change only declared test/production
paths. The controller computes and separates inverse code and test patches.
Return status=ok, instruction, removed_implementation_areas, preserved_api,
target_command, regression_command, build_command. Commands are argv arrays;
use {python} for Python and {report} for explicit non-pytest JUnit output.
Do not commit or create your own solution patch.
