Implement the supplied query on the unchanged base. Testcase is evidence, not
authority to weaken the contract. Modify only production_paths; do not edit tests,
hard-code test inputs, change runner selection or delete protected behavior.
Return status=ok, summary, build_command (argv or [] if inapplicable).
Edit workspace OR return patch, not both. Do not commit or modify .git.
The controller independently performs six fresh-state checks and derives patches.
