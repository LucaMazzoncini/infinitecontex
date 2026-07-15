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

Export format: gzip tarball containing only `.infctx/`, allowing direct import on another machine.
