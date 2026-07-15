# ADR 0008: Deterministic Task-Context Resolution

- Status: Accepted
- Date: 2026-07-15

## Context

Strict plan revisions declare repository paths, symbols, constraints, and model-profile identities, but a declaration is not usable context until it has been resolved against one exact repository state. Resolution must be inspectable, repeatable, bounded, and safe for untrusted repositories. It must not import source modules, execute repository content, contact a model, or infer undeclared context.

## Decision

Task-context inspection is implemented in the focused `task_context` package. Its semantic inputs are an immutable plan revision, a deterministic repository snapshot, an exact digest-bound `ModelProfile`, the estimator version, ranking and packing versions, and the built-in resolution policy. Creation timestamps and machine-specific absolute repository paths are excluded from semantic fingerprints.

The repository inventory prefers Git-tracked files and explicitly permitted untracked files. Git-ignored files, `.git`, `.infctx`, virtual environments, caches, build output, generated files, unsafe links, binary content, and over-limit content are excluded or receive explicit outcomes. Inventory paths are sorted repository-relative POSIX paths and content uses SHA-256. Dirty, staged, commit, branch, case-policy, symlink-policy, tracked-state, and relevant-untracked-state facts are captured in the snapshot. A changed commit or included content therefore changes its fingerprint.

Windows separators are normalized to `/`, so `src\infinitecontex\cli.py` and `src/infinitecontex/cli.py` identify the same relative path. Empty paths, NUL/control characters, traversal, device paths, UNC paths, drive changes, repository escape, invalid ranges, and inappropriate wildcards fail closed. Absolute paths are accepted only when they resolve inside the selected root. Files are never reached through unsafe symlinks, junctions, or reparse-point escapes. Case collisions are reported according to the persisted case policy rather than silently choosing a file.

Python symbols are indexed with the standard-library `ast` module once per source file per analysis. Modules, classes, top-level and nested functions, async functions, methods, properties, constants, fields, and tests carry source spans, decorators as inert metadata, hashes, and resolver fingerprints. Modules are never imported; decorators and dynamic behavior are never evaluated. Duplicate or unqualified matches remain ambiguous. Syntax errors are explicit outcomes.

Other-language exact files and explicit source ranges remain resolvable. C#, Unity, JavaScript, TypeScript, and other languages without an adapter return `unsupported_language` or `resolver_unavailable` for symbols. Required unsupported symbols fail context fit; optional ones remain warnings. Text search is not represented as semantic resolution. C# semantic resolution is deferred to a future Roslyn adapter behind the same resolver protocol.

Resolved source is read once within byte limits, estimator-normalized, hashed, and frozen in memory for candidate construction and packing. The compact persisted analysis stores paths, ranges, counts, hashes, fingerprints, and outcomes, but no full source by default. A hash change between inventory and resolution is a stale-snapshot outcome.

Affected, forbidden, required, output, and test scopes are compared deterministically. Direct overlap, forbidden ancestors, required-file exclusion, duplicate/redundant scopes, provable glob overlap, output escape, case conflict, and unsafe root-wide affected declarations are reported. Scope declarations remain constraints; they grant no capability.

Candidates are created only from the task contract and explicit path, symbol, test, documentation, decision, diagnostic, dependency-output, and conversation declarations. No dependency traversal, semantic retrieval, or unrelated repository discovery occurs. Candidate IDs and fingerprints are content-addressed; the existing ranker and packer retain an auditable duplicate manifest and prefer mandatory evidence.

Context fit requires an exact persisted profile ID or explicit model selection and a verified digest. Weak identity, digest mismatch, unavailable or hard-stale profiles fail closed. The budget calculator uses verified operational limits, never advertised capacity. The existing estimator, calculator, ranker, packer, and manifest store are reused. A passing result requires all required references, conflict-free scopes, all mandatory candidates, and a packed total within the maximum recommended input. Boundary and hard-limit cases remain explicit decisions.

Analyses are written separately from immutable plans at `.infctx/plans/<plan-id>/analyses/<revision>/task-context/`. Immutable analysis files and atomic per-task current pointers use deterministic sorted JSON. The semantic analysis fingerprint excludes its timestamp and includes plan/task/graph, snapshot and frozen-source, profile/digest, estimator, resolver policy, ranking, packing, and manifest identities. Changes to any semantic input make an older analysis stale.

Task context fit is inspection, not runtime admission: it does not construct an outbound request and therefore does not invoke the runtime admission gate. It grants no capability and authorizes no execution. When context does not fit, deterministic machine-readable remediation suggests narrowing ranges, removing optional declarations, separating independent work, resolving missing references, or selecting a larger verified profile. Automatic plan rewriting and task splitting remain deferred.

## Consequences

Equivalent declarations, repository content, policy, and profile identity produce the same analysis fingerprint and decisions. Optional misses remain visible, required misses fail safely, and repository mutation invalidates prior results. The tradeoff is intentionally conservative behavior for dynamic Python and unsupported languages until a deterministic language adapter is implemented.
