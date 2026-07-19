# ADR 0015: Human-controlled immutable plan-state transitions

Status: accepted

## Context

G5 separates validation, evidence, deterministic review, and human acceptance. An accepted review is trustworthy input, but it is not authority to mutate an immutable plan. Criterion satisfaction and task completion require a separate, reviewable state transition governed by the planning state machine.

## Decision

G6 adds a deterministic proposal, approval, and apply workflow. Proposal creation freezes the exact source revision, graph, task, criteria, accepted G5 reviews and decisions, repository snapshots, before/after state, and preview revision fingerprint. Approval is an immutable human decision bound to that exact proposal. Neither step modifies the plan. Only explicit apply revalidates every input and creates exactly revision N+1 through the existing `PlanningService` and `PlanStore`.

Criterion transitions are versioned and explicit. Automated satisfaction requires a fresh accepted supporting review with at least medium rule-based confidence; accepted failing review supports explicit failure. Human-only verification is permitted only for criteria typed `human_approval` and requires a human reason. Waiver, not-applicable, and reopening require an explicit reason. The criterion enum is extended additively with `waived` and `superseded`; legacy values retain their meaning.

Task transitions use the existing authoritative transition map. Completion is limited to `awaiting_review -> completed`, with every mandatory criterion satisfied or waived and every hard dependency completed or superseded. Combined criterion and task changes are visible individually and enter one revision. DAG structure and task count cannot change; full DAG/readiness validation runs on the reconstructed plan.

Proposal, approval, and application records are create-only, atomically persisted, fingerprinted, and compact. Reapplying an already applied proposal returns its existing application rather than creating another revision. Historical revisions remain unchanged.

## Consequences

The graph fingerprint changes when task fingerprints change even though dependency edges remain structurally identical; the application record preserves both lineage and resulting graph identity. G6 does not authorize task execution, grant capabilities, edit source, run commands, mutate Git, access a network, or invoke Ollama/LLMs. Automatic completion and natural-language approval remain forbidden.
