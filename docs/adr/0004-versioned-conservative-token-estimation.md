# ADR 0004: Versioned Conservative Token Estimation

Status: Accepted

Date: 2026-07-15

## Context

Ollama does not provide a universal preflight token-count endpoint, and a model-specific tokenizer may not be installed locally. Making exact tokenization the default today would add a large dependency or an implicit asset download, conflict with offline operation, and still be unreliable across model builds. The M2 E2 fallback of one token per normalized UTF-8 byte was safely simple but wastes too much context for ordinary code and documentation.

Estimated counts must remain distinguishable from counts measured by a provider. Estimator corpus validation is also different from model/runtime calibration and hardware calibration: it compares static text against one pinned tokenizer, not an Ollama template, configured context, throughput, or device behavior.

## Decision

- Retain `normalized-utf8-byte-upper-bound-v1` unchanged for persisted-profile and caller compatibility.
- Use `conservative-mixed-text-v2` for new estimates and newly created conservative profiles. Existing profile files are never mutated automatically.
- Normalize CRLF and lone CR to LF, but never trim or Unicode-normalize text.
- Scan once using integer arithmetic. ASCII alphanumeric runs cost `ceil(run_length / 8)`; ASCII punctuation (including underscores) costs one token each; horizontal-whitespace runs cost `ceil(run_length / 8)`; each LF costs one. BMP non-ASCII and CJK code points cost two; supplementary code points cost four; isolated surrogate code points cost three. Non-empty sections add two structural tokens.
- Keep explicit counts as `measured` with `explicit-token-count`. V2 results remain `heuristic` and expose their strategy, version, normalization, content class, and conservatism label.
- Bound diagnostic file reads at 8 MiB by default. `--allow-large-file` is an explicit override; input is never silently truncated.

The implementation is linear in Unicode code points and uses no content-matching regular expressions, network access, Ollama process, floating-point decision arithmetic, or model-specific asset at runtime.

## Corpus and reference provenance

The committed corpus consists solely of small synthetic fixtures described in `tests/fixtures/token_estimator/README.md`. Reviewed golden counts were generated with `tokenizers.Tokenizer.encode(add_special_tokens=False)` from the official `Qwen/Qwen2.5-Coder-7B-Instruct` `tokenizer.json` at revision `0b044f5762b0be9c48fc2b7bc216454c5eff0bb8`. The asset SHA-256, generation timestamp, normalization, and exact regeneration command are embedded in the golden JSON.

Regeneration is explicit and accepts a local tokenizer asset. Normal package use and automated tests neither install a tokenizer nor download an asset. The Qwen reference is useful target-family evidence, not proof that all supported models tokenize identically.

## Invariants and thresholds

For every golden entry:

```text
estimated_tokens >= reference_tokens
```

Empty input produces zero. Safety comparisons use integer multiplication or basis points. The committed validation requires:

- aggregate estimate/reference ratio at most 2.00;
- ordinary-content aggregate ratio at most 1.75;
- worst ordinary-sample ratio at most 2.50;
- worst difficult-Unicode/whitespace ratio at most 6.00;
- ordinary v2 estimate at most 70% of the legacy byte estimate.

An upper-bound or efficiency-threshold violation fails tests. Threshold changes and golden changes require normal Git review.

## Consequences

Common project content receives materially more usable budget while remaining conservative on the representative corpus. Difficult Unicode remains intentionally more pessimistic. Corpus validation must not be presented as exactness, model calibration, or hardware calibration.

A future exact tokenizer adapter can implement the existing estimator protocol under a new versioned strategy. Runtime context gating remains a separate milestone; this ADR changes inspection estimates only.
