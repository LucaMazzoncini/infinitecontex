# Explicit task execution authorization

G7 adds a human-controlled offline authorization envelope around existing G2, G3, and G4 workflows. A ready or in-progress task still cannot execute without a separately approved and activated grant.

Authorization JSON names exact canonical tools, action kinds, repository scopes, approved proposal IDs, and finite limits. It does not permit wildcards, raw commands, environment interpolation, or external includes.

```text
infctx plan execution authorize PLAN --task TASK --file authorization.json
infctx plan execution approve PLAN PROPOSAL --actor User --reason "Reviewed"
infctx plan execution activate PLAN PROPOSAL
infctx plan execution-session start PLAN --grant GRANT
infctx plan execution run PLAN --session SESSION --action-file action.json
infctx plan execution-session close PLAN SESSION --actor User
```

Inspect with `plan execution-authorization list/show`, `plan execution-grant list/show/revoke`, and `plan execution-session list/show`. Proposal, approval, activation, and session start execute no tool. G3/G4 actions still require their exact earlier human approval. Closing or revoking changes no task or criterion state.

Grant state and counters live separately from immutable authorization identity. Rejected admission consumes no allowance; dispatched attempts do. Duplicate action IDs return the existing journal entry. Mutation consumes the grant conservatively, so later validation requires a new authorization cycle.
