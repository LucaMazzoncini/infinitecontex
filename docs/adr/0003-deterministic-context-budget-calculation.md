# ADR 0003: Deterministic Context-Budget Calculation

Status: Accepted

Date: 2026-07-14

## Context

Model context capacity is finite, and admission decisions must be reproducible before inference. An LLM cannot reliably judge its own remaining context. The persisted model profile already separates advertised, configured, and operational capacities and defines fixed reserves.

Exact provider tokenization is not yet universally available before an Ollama request. Explicit measured counts and conservative estimates therefore need distinct provenance.

## Decision

- Perform all admission arithmetic with non-negative integer token units. Advertised capacity is diagnostic only; operational capacity is the calculation basis.
- Revalidate for every calculation that maximum recommended input plus all fixed reserves does not exceed operational capacity.
- Treat requested output and tool-result allowances as covered by their profile reserves. An allowance larger than its reserve is invalid; a smaller allowance does not silently increase input capacity.
- Preserve each context section independently with category, stable name, token count, provenance, source reference, mandatory/removable state, and retention priority.
- Accept explicit counts as `measured`. For text without a measured count, normalize CRLF and CR to LF and use the normalized UTF-8 byte count as a deliberately conservative deterministic upper estimate. It is never described as exact.
- Use integer utilization basis points. Below the configured warning threshold is `pass_target`; fitting at or above it is `pass_hard`. Overflow that can be corrected by removing optional sections is `repack_required`; otherwise it is `split_required`. Invalid identity or arithmetic is `blocked`.
- Produce inspection results only in M2 E2. Do not wire these decisions into model-call blocking until the later hard-gate slice is implemented and tested.

## Invariants

```text
maximum_recommended_input + fixed_reserves <= operational_context
total_input <= maximum_recommended_input  # required for a pass decision
requested_output <= reserved_output
requested_tool_results <= reserved_tool_results
remaining_input = max(0, maximum_recommended_input - total_input)
```

Exact-boundary input fits with warning pressure; one-token overflow does not. Duplicate section names are invalid because merging them could conceal accounting errors.

## Consequences

- Identical profile, sections, allowances, threshold, and timestamp produce identical results.
- Measured and heuristic counts remain inspectable and can later be replaced section-by-section by a model tokenizer.
- Hardware calibration can update the operational values in a new profile without changing calculator semantics.
- This milestone does not persist results, call Ollama, reduce context automatically, or enforce runtime admission.
