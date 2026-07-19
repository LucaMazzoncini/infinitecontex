# Validation evidence review

G5 reviews typed G4 validation evidence against one exact persisted acceptance criterion. It does not run validation and cannot change criterion or task status.

## Deterministic contract

A review verifies evidence, execution, proposal, approval, command-definition, plan, task, criterion, repository snapshot, output hashes, and exit classification. The criterion must use an exact supported verification method and evidence type. Evidence must be explicitly linked to the criterion; chat, logs, task prose, and model claims are never evidence.

Outcomes distinguish support, support with warning, failure, inconclusive infrastructure results, missing or insufficient evidence, irrelevance, staleness, contamination, conflicts, duplicates, unsupported criteria, and invalid records. Duplicate evidence remains reported and cannot meet counts twice. Conflicting evidence remains visible. Confidence is `high`, `medium`, or `none`, derived entirely from policy rules.

## Offline workflow

```powershell
# G4: run an exact approved validation proposal and persist criterion-linked evidence
infctx plan validation run PLAN_ID PROPOSAL_ID --criterion CRITERION_ID
infctx tool evidence show EVIDENCE_ID

# G5: review typed evidence against the exact criterion
infctx plan evidence review PLAN_ID --task TASK_ID --criterion CRITERION_ID
infctx plan evidence review PLAN_ID --task TASK_ID --criterion CRITERION_ID --evidence EVIDENCE_ID --json
infctx plan evidence-review show PLAN_ID REVIEW_ID

# Record human judgment about the review
infctx plan evidence-review accept PLAN_ID REVIEW_ID --actor "User" --reason "Evidence checked"
infctx plan evidence-review reject PLAN_ID REVIEW_ID --actor "User" --reason "Coverage is insufficient"
infctx plan evidence-review-decision list PLAN_ID
infctx plan evidence-review-decision show PLAN_ID DECISION_ID
```

The human output always states `Criterion status changed: NO` and `Task status changed: NO`. A decision view states that the decision does not complete the criterion or task. JSON includes every accepted, rejected, duplicate, and conflicting contribution.

## Persistence and freshness

Reviews live under `.infctx/plans/PLAN_ID/evidence-reviews/REVISION/TASK_ID/`. Decisions live under `.infctx/plans/PLAN_ID/evidence-review-decisions/`. Records use atomic create-only sorted JSON and semantic fingerprints. Policy, plan, graph, task, criterion, or selected-evidence changes invalidate an older review. Raw output and secrets are not copied into review records.

The built-in bound is 1,000 evidence records per review. Low-level policy coefficients are not user-configurable.

## G6 consumption boundary

An accepted G5 review can support a G6 transition proposal only after its exact review, human decision, evidence lineage, plan/task/criterion fingerprints, repository snapshot, outcome, and confidence are revalidated. Review acceptance still does not alter state; a separate approved G6 apply is required.
