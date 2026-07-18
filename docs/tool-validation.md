# Bounded validation commands (G4)

G4 offers an offline, task-bound validation workflow.

```powershell
infctx validation command list
infctx validation command show python.pytest
infctx plan validation propose PLAN_ID --task TASK_ID --command python.pytest --param target=tests/unit/test_x.py
infctx plan validation show PLAN_ID PROPOSAL_ID
infctx plan validation approve PLAN_ID PROPOSAL_ID --actor "User" --reason "Reviewed"
infctx plan validation run PLAN_ID PROPOSAL_ID
infctx tool validation list
infctx tool validation show EXECUTION_ID
infctx tool evidence list
```

The task must request `run_tests`, `run_build`, or `execute_commands` as appropriate and have a current fitting context analysis. Listing, proposing, showing, approving, and rejecting never start a process. Run requires the exact immutable approval.

Profiles are fixed to `python -m pytest`, Ruff format/lint checks, `python -m mypy src`, and `python -m build`. Parameters are typed; pytest accepts at most 64 repository-relative targets within affected scopes. There is no raw-command option and no shell.

Defaults are a 300-second timeout, a 1,800-second schema hard ceiling, five-second termination grace, 2 MiB per output stream, 50,000 lines, 128 arguments, and 1,000 characters per argument. Output is hashed, bounded, and redacted; truncation is explicit. The environment is minimal and secret-shaped variables are removed.

Validation runs in the live repository. Inventories before and after detect unexpected changes. Cache/build prefixes declared by the exact profile may be transient; other changes classify the run as `repository_mutation_detected`, suppress passing evidence, preserve user files, and require inspection. Evidence never changes task or criterion status.
