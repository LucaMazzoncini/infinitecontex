# ADR 0017: Supervised local-agent runtime

Status: accepted for G8.

G8 adds a supervised local caller while keeping `future_agent` forbidden. A human must create and approve a G7 authorization, activate its exact grant, start its session, create the run, and explicitly invoke `agent run`. Existing grants remain human-only unless their immutable authorization explicitly includes `supervised_local_agent`.

The runtime binds one persisted plan revision, task fingerprint, repository snapshot, task-context analysis, G7 grant/session, ModelProfile fingerprint, Ollama model name, and immutable digest. There is no fallback, download, remote provider, shell, Git mutation, or dependency installation. Repository text and tool results are marked as untrusted data and cannot alter the response schema, caller, grant, scopes, or budgets.

Each response is exactly one strict JSON object: a bounded G2 read/search request, a G3-compatible mutation candidate, a human checkpoint, or a final report. Malformed responses consume model-call budget but dispatch no tool. Every read is admitted again by G7 and G2. Results are returned transiently to the loop while journals retain hashes and counts rather than source content.

Hard runtime limits cap model calls, actions, invalid responses, returned bytes, output tokens, mutation candidates, and wall time. Context is reconstructed from typed state and bounded history for each step. Model output is not claimed to be bit-for-bit deterministic; identities, validation, admission, accounting, and persistence are deterministic.

A mutation candidate uses the existing G3 structured-operation schema and allowlisted G4 command identities. It is persisted as data and immediately stops the loop at `awaiting_mutation_review`. The model cannot propose approval, apply the mutation, run validation, accept evidence, or transition task state.

Run identities are immutable and steps append under `.infctx/plans/<plan>/agent-runs/<run>/`. Raw chain-of-thought, secrets, unlimited prompts, and full tool output are not persisted. Resume is allowed only at persisted boundaries while the grant and snapshot remain current.

The first benchmark is an original disposable asynchronous scheduler rather than a user repository. Its hidden evaluator lives outside the model-visible scope, and Phase A stops before mutation approval.
