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

Task-context records use sorted deterministic JSON and do not duplicate full source by default. Malformed, unsupported-schema, fingerprint-mismatched, or pointer-mismatched data fails closed. Plan revisions are never changed by analysis persistence. Repository content, task, profile, estimator, ranking, packing, or resolution-policy changes make an older analysis stale; stale records remain inspectable and are not silently deleted.

Export format: gzip tarball containing only `.infctx/`, allowing direct import on another machine.
