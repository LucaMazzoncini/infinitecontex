# ADR 0014: Deterministic validation-evidence review

Status: accepted

## Context

G4 persists typed validation evidence, but execution success is intentionally distinct from acceptance-criterion satisfaction. Evidence can be stale, contaminated, duplicated, conflicting, incomplete, or linked to a different task or command. Treating a passing exit code as task completion would collapse execution, review, human judgment, and plan transition into one unsafe action.

## Decision

G5 adds an offline `evidence_review` domain. A versioned policy verifies G4 evidence and its execution, proposal, approval, command-definition, plan, task, criterion, repository-snapshot, output-hash, and classification linkage. Reviews use exact structured criterion links and supported verification-method/category mappings; they never use keyword similarity or an LLM.

Evidence from a different semantic repository state is stale. Unexpected mutation is contaminated. Launch errors, timeout, cancellation, and incomplete output are inconclusive; validation failures produce a failed review. Exact semantic duplicates are visible but counted once. Passing and failing evidence on one snapshot is conflicting. The conservative `all_required` aggregation counts unique accepted evidence and derives its minimum from mandatory task evidence requirements.

Confidence is rule-based: high for exact, fresh, complete, uncontaminated support or a trustworthy validation failure; medium for supported evidence with non-critical redaction warnings; none for invalid, stale, contaminated, conflicting, incomplete, irrelevant, or insufficient evidence. No probability is emitted.

Reviews and human decisions are immutable, fingerprinted, create-only records. A human may accept or reject an exact fresh review, but the decision records only review judgment. It does not mutate the plan, satisfy the criterion, complete the task, grant a capability, approve a tool proposal, or create a commit.

## Consequences

The review workflow is deterministic, auditable, stale-aware, and bounded to 1,000 evidence records. Exact criterion linkage is required in G5; inferred free-text relevance and configurable aggregation logic remain unavailable. Review records contain compact provenance and contribution metadata, never raw validation output, source content, secrets, or environment values.

No command, subprocess, shell, network, Ollama/LLM, Git operation, source mutation, automatic criterion transition, or automatic task transition is introduced. A later milestone may define a separate immutable state-transition workflow for accepted reviews.
