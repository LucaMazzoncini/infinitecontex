# Storage And File Format Guide

Primary state root: `.infctx/`

Strict task-DAG plans are stored separately from legacy captured task strings under `.infctx/plans/<plan-id>/`. Full deterministic revisions are immutable files in `revisions/000001.json`; `current.json` is an atomically replaced compact pointer. Revision history is never deleted implicitly. Schema migration begins at version 1, and malformed, unsupported, or fingerprint-mismatched records fail closed.

- `metadata/manifest.json`: schema and storage metadata.
- `metadata/state.db`: SQLite metadata, decisions, events, pins, retrieval FTS index.
- `project/`: project identity metadata.
- `snapshots/*.json`: canonical snapshot records.
- `summaries/`: restore summaries and derived artifacts.
- `events/events.jsonl`: structured event log.
- `graph/context_graph.json`: node-link graph data.
- `retrieval/`: retrieval-related assets.
- `decisions/`: decision artifacts (extensible).
- `working_set/`: terminal and intent state.
- `prompts/*.md`: compiled prompt outputs.
- `exports/*.tgz`: portable exports.
- `model-profiles/*.json`: schema-versioned, digest-bound model identities and conservative operational budgets. Files are deterministically serialized and atomically replaced.
- `plans/<plan-id>/analyses/<revision>/task-context/task-context-<fingerprint>.json`: immutable compact task-context analyses linked to the exact plan revision, repository snapshot, profile digest, estimator, ranking, packing, and policy identities.
- `plans/<plan-id>/analyses/<revision>/task-context/current/<task-id>.json`: atomically replaced pointer to the latest analysis for that task and revision.
- `plans/<plan-id>/splits/proposals/split-proposal-<fingerprint>.json`: immutable deterministic proposal with source identities, compact context analyses, lineage, coverage, dependency rewrites, and the complete proposed plan. Full source content is not embedded.
- `plans/<plan-id>/splits/approvals/split-approval-<fingerprint>.json`: immutable explicit human approval or rejection bound to the exact proposal fingerprint, actor, optional reason, snapshot/profile identities, and application result.
- `plans/<plan-id>/tool-decisions/<revision>/<task-id>/tool-decision-<fingerprint>.json`: immutable compact G1 policy evaluation linked to exact plan/task/tool/registry/repository/analysis/policy/risk identities. It contains no source, secret, handler, command, or execution output.
- `tool-executions/tool-execution-<fingerprint>.json`: immutable compact G2 execution record linked to the exact invocation, definition, registry, snapshot, and optional plan/task. It stores request/query hashes, counts, content hashes, safe codes, and timing, but never source content, snippets, secrets, or full queries.

Task-context records use sorted deterministic JSON and do not duplicate full source by default. Malformed, unsupported-schema, fingerprint-mismatched, or pointer-mismatched data fails closed. Plan revisions are never changed by analysis persistence. Repository content, task, profile, estimator, ranking, packing, or resolution-policy changes make an older analysis stale; stale records remain inspectable and are not silently deleted.

Split proposal and decision records use create-only persistence. Regenerating identical proposal semantics is idempotent; a conflicting record with the same ID fails. Listing, showing, exporting, or validating either record cannot create an approval. An applied approval points to exactly the next immutable revision; the plan store writes the revision create-only and atomically replaces `current.json` while preserving all historical revisions.

Tool decisions also use create-only sorted JSON. Their semantic fingerprint excludes the timestamp. A plan revision, graph, task status/fingerprint/capability/scope, task-context analysis, repository snapshot, model profile, tool definition/version, registry fingerprint, policy version, or risk-derivation change makes an older decision stale. Stale decisions remain visible through list/show and cannot be treated as current authorization.

Tool execution records use create-only atomic sorted JSON and are never silently replaced or deleted. Unsupported schema versions, malformed JSON, invalid fingerprints, and conflicting immutable IDs fail closed. `tool execution list/show` are inspection-only.

Export format: gzip tarball containing only `.infctx/`, allowing direct import on another machine.

G3 adds `plans/<plan-id>/mutations/proposals/`, `plans/<plan-id>/mutations/approvals/`, and `tool-mutations/`. Proposal and approval files are immutable sorted JSON. Mutation execution records contain exact linkage, paths, hashes, counts, status, rollback state, and timestamps but no source contents or full diff. Existing `.infctx` state is preserved.


## G4 validation state

Plan-scoped immutable proposals and decisions live under `plans/PLAN_ID/validations/`. Compact execution records live in `tool-validations/`; optional review-only evidence lives in `validation-evidence/`. The records store fingerprints, snapshots, bounded redacted previews and classifications, never raw environments, unlimited output, source contents, or secrets.

## G5 evidence-review state

Immutable reviews live at `plans/PLAN_ID/evidence-reviews/REVISION/TASK_ID/`; immutable human decisions live at `plans/PLAN_ID/evidence-review-decisions/`. Sorted atomic JSON stores exact plan/task/criterion/evidence provenance, contribution dispositions, outcome and confidence, but no raw output, source, secrets, or environment values. Fingerprints make policy, plan, task, criterion, or evidence changes stale and fail closed.

## G6 transition state

Create-only proposal, approval, and application JSON records live under `plans/PLAN_ID/state-transition-proposals/`, `state-transition-approvals/`, and `state-transition-applications/`. Records preserve source/result revision lineage, exact review-decision fingerprints, state changes and actors without raw evidence, output, source, environments, or secrets. The existing revision store remains authoritative.

## G7 task execution records

Per-plan additive directories `execution-authorization-proposals`, `execution-authorization-approvals`, `execution-grants`, `execution-grant-revocations`, and `execution-sessions` store sorted atomic JSON. Grant counters are mutable atomic state records; action journals are append-only and omit payload content.

## G8 agent runs

Agent identity is stored at .infctx/plans/<plan-id>/agent-runs/<run-id>/run.json; compact append-only steps are under steps/, and an optional unapproved candidate is mutation-candidate.json. Records contain hashes and accounting, not chain-of-thought, secrets, or unrestricted tool output.

## G9 model-call evidence

Each supervised call adds one immutable JSON record under plans/<plan>/agent-runs/<run>/calls/. It links the authoritative global context-manifest and context-admission records to the run and step, with hashes, counts, exact model identity, budgets, and requested/effective/transmitted sampling; prompt and repository bodies are omitted.