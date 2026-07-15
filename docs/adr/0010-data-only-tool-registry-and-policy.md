# ADR 0010: Data-only tool registry and deterministic policy

## Status

Accepted for G1.

## Context

Future agent execution needs stable tool identities and explainable policy decisions before any handler can be trusted. A Python callable registry would mix description, authorization, and execution, allow import side effects, and make offline review unreliable. Planning already owns task capability requests, while task-context resolution owns repository path and scope safety.

## Decision

G1 adds `infinitecontex.tools` as a data-only package. A tool has a deterministic ID derived from canonical name and semantic version, a SHA-256 definition fingerprint, strict versioned input/output field schemas, explicit effects, planning capabilities, repository scope semantics, effect-derived risk, availability, and a predicted approval class. Definitions are frozen Pydantic contracts. Registration order cannot affect list order, export bytes, or registry identity.

Input/output fields reject unknown contract attributes, duplicate names, opaque objects, invalid defaults, impossible bounds, unsafe names, and sensitive fields that are not excluded from logs. No definition contains an executable handler. The future provider protocol accepts already-instantiated definitions only; G1 does not discover or import plugin code.

`planning.Capability` remains authoritative. G1 adds the missing artifact-write, plan-mutation, and task-status-mutation declarations to that enum. A task request, a tool requirement, structural eligibility, a grant, execution admission, and execution are distinct states. G1 implements only the first three. Grants remain empty and `executable_now` is always false.

Tool effects are explicit booleans. Contradictions fail registration. Risk floors are deterministic: read-only is low; repository/`.infctx` writes are at least medium; processes, network, model and Git mutation are at least high; destructive, external-path, secret, elevated, irreversible, history-rewrite, and force-push effects are critical. A supplied risk cannot understate the floor. Destructive/history/secrets declarations are permanently forbidden.

Scope evaluation reuses task-context repository normalization and matching. Repository escape, unresolved scope, broader-than-task write scope, type mismatch, and forbidden overlap fail closed. Repository-dependent tools require a current passing task-context analysis.

The policy order is exact identity, definition validity, availability/permanent restrictions, plan/task state, context linkage/freshness/fit, requested capabilities, scopes, risk, approval class, structural eligibility, and the unconditional false execution flag. Earlier hard denials cannot be overridden. Semantic decision fingerprints exclude timestamps.

Compact immutable decisions are stored at `.infctx/plans/<plan-id>/tool-decisions/<revision>/<task-id>/<decision-id>.json`. They link exact plan, graph, task, registry, tool, repository, analysis, policy, and risk identities. Sorted create-only JSON contains no source, secret, or tool output. Currentness is recomputed; stale records remain inspectable but cannot represent current policy.

## Consequences

The CLI can list/show/export the registry and evaluate or inspect task/tool decisions entirely offline. Read-only definitions may be structurally eligible for a future request. Mutation and execution definitions report missing grants and future approval requirements. Permanent restrictions cannot be downgraded by configuration.

No handler, command execution, tool result, capability grant, approval action, Ollama construction, network request, source edit, arbitrary plugin import, or automatic plan change is introduced. A later execution gateway must separately implement grants, admission, handler binding, sandboxed execution, and evidence capture.
