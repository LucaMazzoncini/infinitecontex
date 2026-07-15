# ADR 0009: Bounded structural task splitting with explicit application approval

- Status: Accepted
- Date: 2026-07-15

## Context

Task context analysis can prove that required evidence exceeds the selected model profile. Passing that task to a model would violate the hard context-budget gate. A split must preserve the task contract, dependency ordering, exact repository snapshot, and model-profile identity; an LLM assertion that children fit is not sufficient.

The existing planning package already owns strict DAG validation and immutable plan revisions. The task-context package already owns repository resolution, context packing, and exact-profile fit decisions. Reimplementing either concern inside a splitter would create conflicting sources of truth.

## Decision

Add `task_splitting` as an orchestration package over planning and task-context services.

The first deterministic rule partitions explicit required context declarations into stable evidence groups. Each proposed leaf is inserted into a transient strict plan and evaluated through `TaskContextService.context_fit_revision`. A proposal is valid only when every leaf passes, contract coverage is complete, the DAG validates, and recursion remains within versioned depth, child, and descendant limits.

The source task becomes a completion barrier with its stable key. Children inherit the source task's upstream dependencies; downstream tasks continue to depend on the stable barrier. This avoids silently weakening dependency ordering. Contract fields remain recorded on the barrier and are copied to leaves with an explicit coverage report.

Proposals and approvals are immutable JSON artifacts below the existing plan directory. Proposal fingerprints exclude wall-clock identity fields and transient context-analysis IDs. Applying a proposal requires an explicit human actor and reason, exact source revision and graph fingerprints, and an unchanged repository snapshot. Warnings require explicit acknowledgement. Only application creates a new plan revision.

The splitter fails closed when a task has fewer than two structural context groups, a leaf has a non-size blocker, a whole evidence unit remains oversized, bounds are exhausted, or proposal inputs become stale. It does not ask a model to invent path boundaries and does not grant capabilities.

## Consequences

- Existing plan and context-fit behavior remains authoritative and reusable.
- Split proposals are inspectable, repeatable, and cannot authorize execution.
- Stable completion barriers preserve downstream dependencies at the cost of an explicit integration node.
- The initial structural rule cannot split one inherently oversized file or symbol. Such tasks return a remediation requiring a narrower source range, a safer interface boundary, or future language-aware structural analysis.
- CLI presentation and richer architectural partition rules remain separate vertical slices.
