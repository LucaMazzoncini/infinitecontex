# ADR 0013: Allowlisted bounded validation commands

Status: Accepted (G4)

## Decision

G4 executes only five built-in, versioned validation profiles: pytest, Ruff format check, Ruff lint, mypy, and Python package build. They bind exactly to the promoted G1 identities `execution.run-tests`, `execution.run-build`, and `execution.run-static-analysis`. The generic bounded-shell definition remains declared-only.

A strict command definition freezes the current Python executable identity, module, ordered argument array, typed parameters, repository working directory, minimal environment fingerprint, timeout, output limits, accepted exit codes, transient-output prefixes, and mutation policy. Definitions are data, registration-order independent, and cannot contain arbitrary command strings. Execution uses direct process creation with `shell=False`; shell wrappers, raw commands, option injection, interactive input, dependency installation, network policy, Git mutation, source-edit commands, and model selection remain unavailable.

## Workflow and authorization

Execution is task-bound: an exact plan revision, eligible task, fresh fitting task-context analysis, repository snapshot, requested capability, G1 policy decision, tool fingerprint, command fingerprint, executable fingerprint, arguments, environment, and limits are frozen in an immutable proposal. Proposal/list/show launch nothing. A human records one immutable approval or rejection. Only `run` accepts an approved proposal, reconstructs and verifies every bound value, then creates the invocation-scoped authorization implicit in the admitted frozen proposal. Capability requests never become reusable grants, and task state or acceptance criteria never change automatically.

## Runtime safety

The environment retains only a small platform/tool subset and removes secret-shaped names; records contain names and a fingerprint, never values. Stdout and stderr are drained separately, hashed, line/byte bounded, decoded safely, and conservatively redacted. Truncation and redaction are explicit. Timeout requests graceful termination and then forced termination after a bounded grace period; process groups are used where supported. This is best-effort process-tree control, not a cross-platform containment guarantee.

Execution occurs in the live repository. Exact inventories before and after detect semantic source changes and unexpected files; only declared cache/build prefixes are ignored. A contaminated run is not passing evidence and user data is never reverted automatically. Network isolation is not claimed at OS level, so only installed offline validation modules with narrow behavior are trusted.

## Records and evidence

Create-only deterministic JSON stores proposals and decisions beneath the plan, compact executions under `.infctx/tool-validations/`, and optional passing evidence under `.infctx/validation-evidence/`. Records retain hashes, counts, bounded redacted previews, snapshots, classification, and linkage—not environment values, unlimited output, source content, prompts, or secrets. Evidence is review input only and cannot satisfy a criterion or complete a task.

## Consequences

G4 provides useful tests/build/lint/type checks while preserving explicit human control and fail-closed admission. Live-repository validation can detect but cannot prevent mutation, and redaction cannot be perfect. General shell, network, install, Git mutation, editing, LLMs, autonomous agents, and project-defined executable plugins remain future work.
