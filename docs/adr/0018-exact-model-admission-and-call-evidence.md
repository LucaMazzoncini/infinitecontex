# ADR 0018: Exact model admission and per-call evidence

Status: accepted for G9.

Supervised execution rejects `auto`, aliases, missing digests, and implicit fallback profiles. Operators explicitly create a profile for an installed Ollama tag and expected digest, then verify the stored identity against the current installation without generating text.

Before every supervised model call, the runtime builds the exact prompt, packs it with the existing deterministic context service, persists the immutable manifest, constructs the exact admission request, executes the existing fail-closed `ContextAdmissionGate`, and persists its decision. A compact model-call record binds run, step, grant, profile, manifest, admission, prompt hash, serialized request hash, budgets, and sampling. Failure at any persistence or admission boundary prevents provider invocation.

Sampling uses a typed Ollama contract. Requested, effective, and transmitted values are recorded separately; required values are sent in the native `options` object. G9 supports temperature, top-p, top-k, seed, repeat penalty, stop sequences, output-token limit, and context size. Provider token counts remain absent when Ollama does not report them.

Repository content remains untrusted. It cannot change caller identity, grant membership, model identity, budgets, sampling, or admission. `future_agent`, shell, network tools, Git mutation, automatic validation, automatic approval, and mutation application remain disabled. A valid G3-compatible candidate stops at `awaiting_mutation_review`.

Authoritative manifests and admissions retain their existing global stores. Per-run call evidence lives under `.infctx/plans/<plan>/agent-runs/<run>/calls/` and contains metadata and hashes, not prompt or source bodies.
