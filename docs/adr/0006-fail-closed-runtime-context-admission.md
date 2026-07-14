# ADR 0006: Fail-closed runtime context admission

## Status

Accepted for M2 E4.

## Decision

Every integrated Ollama chat dispatch is owned by `GatedChatDispatcher`. It freezes the final ordered message payload, asks `ContextAdmissionGate` to validate it, and dispatches those exact immutable section values only after admission. There is no force or ignore-budget option.

The built-in `fail-closed-context-admission-v1` policy requires a verified exact model digest, rejects stale profiles, and accepts conservative uncalibrated profiles only when their explicit operational capacity is at most 32K tokens. This is not hardware calibration. Advertised model capacity is diagnostic data and is never an admission budget.

## Why a manifest is insufficient

A valid packing manifest describes historical candidate choices. Runtime system text, history, user input, tools, or candidate content can change afterward. Admission therefore re-parses the persisted manifest, recomputes its canonical SHA-256 fingerprint and ID, validates its profile/model/strategy versions and integer accounting, then independently estimates the actual outbound sections.

Every included candidate must appear exactly once in the frozen request with the same candidate fingerprint and normalized-content SHA-256. Runtime sections are explicit typed buckets; the request fingerprint covers the ordered section IDs, kinds, roles, mandatory flags, normalized content hashes, candidate links, exact model/profile/manifest identity, policy, and requested allowances. Timestamps and correlation IDs do not affect that reproducibility fingerprint.

## Budget and rejection semantics

The existing calculator remains authoritative. It uses the profile's operational capacity and fixed output, tool-result, system-prompt, and safety reserves. A small explicit per-message provider overhead is included. Exact boundaries pass; a one-token overflow, allowance overflow, negative/impossible arithmetic, hidden content, weak identity, stale profile, mismatch, malformed data, or tampering fails before streaming begins.

Rejections are typed, actionable, and written as compact records under `.infctx/context-admissions/`. Records contain hashes, identities, totals, reason codes, warnings, timestamp, and dispatch state—not full prompts. Writes are deterministic JSON and atomic replacement.

## Time-of-check/time-of-use

The dispatcher sends the same frozen strings that were fingerprinted and never rereads source files after admission. Model selection is checked against the installed digest when chat starts. A residual risk remains if an Ollama model tag is replaced after that inspection but before dispatch; a future bounded identity-cache policy may shorten that window without adding a network lookup to every request.

## Deferred work

This decision adds no retrieval, embeddings, summarization, editing, task decomposition, calibration, benchmarking, or autonomous behavior. Those remain separate milestones so admission stays deterministic and auditable.
