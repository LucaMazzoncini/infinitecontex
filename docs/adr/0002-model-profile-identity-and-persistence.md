# ADR 0002: Model Profile Identity and Persistence

Status: Accepted

Date: 2026-07-14

## Context

Ollama model display names are mutable references. Pulling a newer build can retain the same name while changing weights, template, metadata, tokenizer behavior, or runtime characteristics. Applying a prior operational profile solely by display name could therefore use calibration or safety assumptions from a different model build.

Advertised context length describes a model-format capability, not a verified capacity for the configured Ollama runtime and local hardware. Runtime configuration, KV-cache memory, quantization, offload, and provider behavior can make a smaller value necessary.

Profile creation must be quick and deterministic, while hardware calibration is comparatively expensive and environment-specific.

## Decision

- Bind profiles to provider, normalized model name, and the immutable digest returned by Ollama's installed-model metadata whenever available.
- Never fabricate a digest. A profile without one is explicitly `weak` and `unverified`.
- Keep advertised, Ollama-configured, and Infinite Context operational capacities as separate fields. Operational capacity never exceeds configured capacity.
- Create conservative profiles as `uncalibrated`. Calibration is a later, explicit operation with dated evidence and a hardware reference.
- Persist deterministic, human-readable JSON under `.infctx/model-profiles/`. Writes use a same-directory temporary file and atomic replacement.
- Exact lookups never fall back between digests. If the current digest differs, report `digest-mismatched`; explicit creation writes a separate profile and preserves the old one.
- Malformed or unsupported-schema files fail with an actionable error instead of being ignored or overwritten. Version 1 exposes a migration entry point for future additive migrations.

## Consequences

- Repositories carry their local model assumptions alongside other `.infctx` state and remain inspectable/offline.
- Updating a model cannot silently inherit a different build's profile.
- Weak identities can support constrained environments but cannot prove build continuity and remain visibly unverified.
- Profile creation provides conservative defaults without claiming benchmark or hardware evidence.
- The runtime hard context gate and calibration feedback remain separate M2 work.
