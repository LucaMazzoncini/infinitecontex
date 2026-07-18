# ADR 0012: Approved transactional repository text mutations

Status: Accepted for G3.

## Context

G1 established immutable tool identity and structural policy; G2 established exact handler binding, repository-scoped admission, path safety, snapshot freshness, and content-free execution records. The first write slice must not turn a capability request or a preview into permission to edit.

## Decision

G3 promotes only the existing structured source-patch, test-write, and documentation-write declarations. It supports exact line-range replacement and new UTF-8 text-file creation. Delete, rename, binary, configuration, shell, Git, network, model, and autonomous operations remain disabled.

Proposal, approval, and apply are separate immutable states. A proposal binds the exact plan revision, task/context identities, tool fingerprint, repository snapshot, capabilities, scopes, ordered operations, preimage/postimage hashes, byte counts, and deterministic unified diff. Proposal fingerprints exclude timestamps and display-only diff formatting. Proposal generation and approval never modify targets.

Only an explicit human approval for the exact proposal permits apply. Apply reloads and validates every identity, recomputes policy, verifies protected paths and exact preimages, constructs all postimages and temporary files before mutation, then applies targets in deterministic order. Each replacement is atomic. Multi-file behavior is rollback-protected rather than an OS-level transaction: failures restore existing byte preimages, remove newly created targets, and verify rollback hashes. The residual risks are a process or machine failure between cross-file replacements and the race after the final verification.

Sensitive, ignored, internal, external, generated, cache, virtual-environment, unsafe-link, ambiguous, and configuration targets fail closed. Deterministic classification maps source, test, and documentation paths to their existing requested capabilities. Requests never become persistent grants and task status or criteria never change automatically.

Proposals and approvals are create-only under the plan. Compact apply records under `.infctx/tool-mutations/` contain linkage, paths, hashes, counts, status, rollback state, and timestamps, but no source content or full diff.

## Consequences

The offline CLI can validate a request, preview and persist a task-bound proposal, record an explicit decision, apply it, and inspect safe records. Applying never reparses the diff; structured operations remain authoritative. Broader editing, commands, Git mutation, model-generated approval, chat approval, and autonomous execution remain future work.
