# ADR 0007: Strict persisted task-DAG plans

## Status

Accepted for M3 E1.

## Decision

Project plans are strict, versioned Pydantic contracts whose hard and soft task dependencies form validated directed acyclic graphs. This foundation stores declarations and inspection state only. It does not call a planner model, retrieve evidence, grant permissions, execute tasks, edit files, or infer status from prose.

Legacy snapshot `active_tasks` remain captured intent strings. They are neither migrated nor reinterpreted as executable DAG nodes.

## Identity and fingerprints

When omitted, a plan ID is `plan-` plus the first 24 hexadecimal characters of SHA-256 over the normalized stable plan key, objective, and repository reference. A task ID similarly hashes the plan ID and case-folded task key. Supplied IDs must use the safe identifier grammar.

Task fingerprints cover normalized title, objective and description; type, status and priority; dependencies and parent; structured criteria, evidence, inputs and outputs; scopes; context declarations; complexity/context estimates; requested capabilities; risks/retry declarations; provenance; and bounded metadata. They exclude timestamps and nonsemantic input ordering.

The graph fingerprint covers sorted task IDs/fingerprints, hard edges, soft edges, and parents. Revision fingerprints cover semantic plan state, revision number, previous fingerprint, reason, author, changed tasks, and structural changes. Persisted fingerprints are recomputed on load; tampering fails closed.

## DAG, ordering, and readiness

Every dependency and parent must exist. Self and duplicate edges are invalid. Hard, soft, and parent graphs must all be acyclic. Cycle analysis is iterative: Kahn elimination identifies cyclic residue, then an iterative path walk reports sorted involved IDs and a canonical path rotated to its lexicographically smallest node. No recursive graph traversal is used.

Topological ordering uses Kahn's algorithm and a heap. Simultaneously available nodes sort by lower dependency depth, higher priority, versioned task-type order, case-folded task key, task ID, then task fingerprint. JSON input order and clocks never affect it. Ordering is inspection, not permission to execute.

`ready` means the task explicitly has ready status and every hard prerequisite is completed or superseded. Explicitly blocked tasks and tasks waiting on dependencies are reported separately. Roots, leaves, isolated nodes, direct downstream dependents, completed prerequisite closure, and maximum depth are deterministic inspection fields.

## State, evidence, and capabilities

Allowed transitions are:

```text
draft -> ready | cancelled
ready -> blocked | in_progress | cancelled
blocked -> ready | cancelled
in_progress -> awaiting_review | failed | blocked
awaiting_review -> completed | in_progress | failed
failed -> ready | cancelled
completed -> superseded
```

Cancelled and superseded tasks are terminal. Import never performs transitions. Criteria have typed verification and evidence requirements; their status is stored but never changed automatically. Completion cannot be derived from a model claim.

Capability requests are typed declarations. `granted_capabilities` must be empty in M3 E1. Metadata rejects executable-looking keys; no imported value is executed.

## Revisions and persistence

Revisions are immutable:

```text
.infctx/plans/<plan-id>/revisions/000001.json
.infctx/plans/<plan-id>/revisions/000002.json
.infctx/plans/<plan-id>/current.json
```

A fully written same-directory temporary file is hard-linked to create each revision without overwrite. `current.json` is an atomically replaced compact pointer. N+1 references N and its fingerprint. A timestamp-only or semantically identical import is idempotent and creates no revision. Invalid revisions produce no partial files.

## Strict input and limits

Plan input is UTF-8 JSON only. Markdown fences, duplicate object keys, invalid enums, unknown fields, unsafe scopes, and semantic repair are rejected. Defaults are 8 MiB/file, 10,000 tasks, 64 hard and soft dependencies per task, 64 criteria and evidence declarations per task, 256 total context references, 64 metadata entries, and bounded strings. Scope strings cannot be absolute, traverse with `..`, or contain controls.

The future planner-output envelope contains a request fingerprint, strict plan, assumptions, unresolved questions, risks, decomposition rationale, omissions, provenance, and confidence. It has no free-form control channel. Representing future LLM provenance does not mean an LLM was called here.

## Future integration

Later M3 slices may resolve declared paths and symbols and validate task context fit through the existing packer/admission components. Those services will consume these declarations without changing DAG identity or bypassing revision validation.
