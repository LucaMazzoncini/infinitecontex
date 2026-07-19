# Human-controlled plan-state transitions

G6 turns accepted G5 evidence-review decisions into explicit plan-state transition proposals. Acceptance of a review alone never changes a plan.

## Workflow

```powershell
# Existing G4/G5 evidence and accepted review
infctx plan evidence-review show PLAN_ID REVIEW_ID

# Propose a criterion transition, optionally combined with legal task completion
infctx plan transition propose PLAN_ID --task TASK_ID --criterion CRITERION_ID --to satisfied --review REVIEW_ID --review-decision DECISION_ID --task-to completed
infctx plan transition-proposal show PLAN_ID PROPOSAL_ID --json

# Human decision still does not change the plan
infctx plan transition approve PLAN_ID PROPOSAL_ID --actor "User" --reason "Evidence and state reviewed"

# Only apply creates revision N+1
infctx plan transition apply PLAN_ID PROPOSAL_ID
infctx plan show PLAN_ID --revision 2

infctx plan transition-approval list PLAN_ID
infctx plan transition-application show PLAN_ID APPLICATION_ID
```

The preview states `Plan changed during proposal creation: NO`, `Task execution authorized: NO`, and `Source files changed: NO`. Approval records only the decision. Apply uses the structured proposal, never reparses human output.

## Policy

Automated `satisfied` requires an exact fresh accepted G5 review supporting the criterion. `failed` requires an accepted failing review. Criteria typed `human_approval` may be satisfied directly only with an explicit human reason. Waiver, not-applicable, and reopening require reasons. No LLM actor is accepted.

Task status follows the existing planning transition graph. Completion requires `awaiting_review`, all mandatory criteria `satisfied` or `waived`, completed/superseded hard dependencies, and a valid reconstructed DAG. Optional pending criteria remain visible. There is no ready-to-completed shortcut.

Records live under `.infctx/plans/PLAN_ID/state-transition-{proposals,approvals,applications}/`. They contain fingerprints and lineage, not source, evidence output, secrets, or environments. Repeated apply is idempotent.
