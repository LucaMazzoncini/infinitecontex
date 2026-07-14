# ADR 0005: Deterministic Context Ranking and Packing

Status: Accepted

Date: 2026-07-15

## Context

InfiniteContext needs the smallest sufficient evidence set for a finite model input. Candidate selection affects safety and reproducibility, so neither an LLM nor input enumeration order can control it. The existing model profile, versioned token estimator, and integer context-budget calculator already provide the authoritative capacity boundary.

## Decision

Use `deterministic-context-ranking-v1` with immutable candidates and integer score components. Ordering uses these tiers:

1. mandatory instructions and evidence;
2. current task, user request, and instruction contracts;
3. direct references;
4. tests and active errors;
5. changed source and Git evidence;
6. source, symbols, and direct dependencies;
7. decisions and task state;
8. documentation;
9. conversation, tool results, and miscellaneous support.

Within a tier, the score combines built-in category priority, direct match, caller-supplied task relevance, dependency distance, changed state, test/error status, explicit recency rank, source confidence, and retention priority. All arithmetic is integer-only. Ties use explicit tie key, candidate ID, then SHA-256 candidate fingerprint. Supplied timestamps are provenance only; only an explicit integer recency rank influences order.

No LLM ranking is used. LLM ranking cannot guarantee stable ties, respect a hard budget by construction, or provide reliable explanations for every omission.

## Deduplication

Equivalence classes are formed in near-linear time from candidate ID, normalized content hash, exact case-folded source range, and explicit logical deduplication identity. The winner is selected by mandatory status, direct-match evidence, retention priority, measured provenance, lower equivalent cost, then fingerprint. Every loser remains in the manifest with `duplicate` and `duplicate_of` fields.

Group identity is recorded but does not impose a group cap in version 1. This avoids inventing semantics for caller-defined groups. A future policy version may add explicit group rules.

## Packing

Use `tiered-greedy-pack-v1`:

- resolve an exact verified, non-stale model profile;
- select the estimator named by that persisted profile;
- ask the existing calculator for the maximum recommended input and the capacity remaining after caller-reserved input;
- include all mandatory candidates or return `mandatory_overflow` with an exact deficit and no partial pack;
- visit optional candidates in rank order, include those that fit, and continue after an oversized candidate;
- never truncate or summarize a candidate.

Advertised context capacity is diagnostic and is never a packing budget.

As a minimal diversity safeguard, optional non-direct bulk categories are capped at 60% of the available pack allowance. The bulk set and cap are recorded in every manifest. Mandatory and direct-match candidates are exempt. No arbitrary single-candidate or group cap is imposed.

## Manifest and persistence

Schema-versioned manifests are stored additively under `.infctx/context-manifests/` using deterministic sorted JSON and same-directory atomic replacement. Inline content is omitted; stable references, content hashes, scores, costs, and reasons are retained. Malformed and unsupported-schema files fail explicitly. No retention deletion occurs in this slice.

The SHA-256 reproducibility fingerprint covers schema/policy/packing versions, exact profile and digest, operational/recommended budgets, caller reservation, estimator identity, safeguards, ranked candidate fingerprints and costs, inclusion order, exclusion reasons, decision, and deficit. `calculated_at` is deliberately excluded. The manifest ID is derived from this fingerprint, so equivalent normalized inputs reproduce the same ID and plan.

## Consequences

Ranking is `O(n log n)` and deduplication uses hash maps rather than pairwise comparison. Every input candidate is included or appears in an audited exclusion. Candidate files and normal manifest operation require neither Ollama nor network access.

This slice creates inspection plans only. It does not inject context into chat, block model calls, retrieve repository evidence, summarize content, or perform autonomous actions. Runtime enforcement remains the next separate context-governor slice.
