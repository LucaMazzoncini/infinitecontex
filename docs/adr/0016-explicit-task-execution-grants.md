# ADR 0016: Explicit task execution grants

## Status

Accepted for G7.

## Decision

Task status, requested capability, structural tool eligibility, evidence, and plan-state transitions are policy inputs; none is execution permission. G7 separates a data-only specification, immutable proposal, human decision, activated grant, session, and one typed action request.

Grants bind the exact plan revision, task and context fingerprints, repository snapshot, tool definitions, scopes, G3/G4 proposal IDs, caller type, action counts, and byte limits. Wildcard tools, unlimited scopes, persistent task capabilities, and future-agent callers are forbidden.

Dispatch is limited to the existing G2 read gateway, an already approved G3 mutation proposal, or an already approved G4 validation proposal. G7 never constructs or approves G3/G4 proposals. Proposal, approval, activation, and session start call no handler. Only `execution run` reserves an allowance and dispatches one action.

Grant identity is immutable while counters are atomically replaced under a repository-local exclusive lease. Rejected admission consumes nothing; a dispatched attempt consumes its allowance. Action IDs are idempotent. Successful mutation marks the grant `consumed_by_mutation`, requiring a new snapshot and grant for subsequent validation.

One session is permitted per grant. The append-only compact journal stores input fingerprints and underlying record references, never source text, snippets, mutation bodies, full validation output, environment values, or secrets. Immutable human revocation prevents new reservations without claiming to kill an already running validation.

No shell, network, Git mutation, model invocation, plan transition, capability delegation, or autonomous action selection is introduced.

## Residual risks

The file lease is local-process coordination, not distributed locking. G2/G4 retain their existing live-repository TOCTOU and mutation-detection controls. If journal persistence fails after dispatch, the allowance remains consumed, deliberately failing closed.
