# ADR 0011: Safe repository read execution

Status: Accepted for G2.

## Context

G1 deliberately kept tool definitions data-only and separated task capability requests, structural policy eligibility, grants, admission, and execution. The first execution slice needs useful repository inspection without turning the registry into an execution framework for writes, processes, Git mutation, network access, or autonomous agents.

## Decision

G2 enables handlers only for the built-in inventory listing, explicit file read, explicit source-range read, path search, and literal text search definitions. Definitions still contain no callable. A separate handler registry binds each callable to the exact tool ID, semantic version, and definition fingerprint; a missing or mismatched binding fails before dispatch.

Every call is represented by a frozen, versioned invocation and must carry a frozen invocation-scoped read grant. The grant is bound to the invocation fingerprint, exact tool definition, repository snapshot, authorization source, and normalized requested scopes. It contains only repository-read authority and cannot be widened or reused after any semantic change. Direct human CLI calls can mint this narrow grant. Task-bound calls additionally require the exact persisted revision/task, a fresh passing task-context analysis, the repository-read request, passing G1 structural policy, and a path inside declared non-forbidden scopes. A request is not stored as a general grant.

Admission is centralized in one gateway: registry and definition validation, implementation state, handler binding, snapshot and grant validation, task admission performed by the service, sensitive-path denial, dispatch, immutable result construction, safe events, and compact record persistence. Rejected admission never calls a handler. Future-agent caller identity is defined but rejected.

Repository access reuses the M3 E2 inventory, POSIX path normalization, containment checks, scope matching, file classification, hashing, binary detection, symlink/reparse protection, and size limits. `.git`, `.infctx`, ignored outputs, repository escapes, unsafe links, and a versioned set of likely secret paths are denied. Sensitive files are filtered before inventory content inspection. There is no security override.

Search is literal, not regular-expression or shell matching. Glob matching applies only to validated inventory paths. Results sort by path, line, and column; offsets provide deterministic continuation. File, match, byte, line, snippet, and output bounds produce explicit partial state rather than silent truncation.

Before returning file content, handlers verify the admitted inventory entry, resolve the same safe path, read once, and compare size and SHA-256 content identity. Deleted, changed, replaced, or retargeted content fails closed. A residual race exists after the final immutable result is returned and before its caller consumes it; the returned hash identifies the exact bytes observed.

Execution records live under `.infctx/tool-executions/` as create-only, sorted JSON. They store identity, authorization, request hashes, status, counts, hashes, and safe codes, never content, snippets, secrets, or full queries. Malformed and unsupported records fail closed.

## Consequences

The human and task-bound workflows are offline and deterministic, while write, patch, shell, subprocess, Git mutation, network, dependency installation, Ollama/model, arbitrary regex, and autonomous selection remain unavailable. This narrow gateway establishes admission and audit contracts for later milestones without granting broader capabilities.
