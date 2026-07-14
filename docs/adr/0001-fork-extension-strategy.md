# ADR 0001: Fork Extension Strategy

Status: Accepted

Date: 2026-07-14

## Context

The local repository is a working Python 3.11+ package and Typer CLI at version 0.3.1. It already owns project scanning, Git working-set capture, snapshots, retrieval, graph artifacts, prompts, restore, decisions, storage, API access, and agent handoff files. Replacing it would duplicate tested behavior and break the established `infctx` command and `.infctx/` storage contracts.

Large Unity/C# repositories will eventually benefit from compiler-grade semantic facts. Python-only parsing cannot reliably reproduce Roslyn symbol binding, but that limitation does not apply to the generic memory engine, Ollama transport, context governance, chat, planning, or tool policy.

## Decision

Extend the existing typed Python modular monolith. New chat and model-provider code depends on protocols and calls existing application services directly. Existing commands, storage, and MIT notices remain intact.

When M5 is reached, use an optional, narrowly scoped Roslyn child-process sidecar for C#/Unity semantic analysis. Python remains the lifecycle, policy, persistence, and product authority. The sidecar will exchange versioned records and degrade gracefully when unavailable; it will not become a second CLI or storage system.

## Consequences

- Upstream compatibility remains practical because additions are isolated and existing service contracts are reused.
- Generic repositories and read-only chat do not depend on .NET or Roslyn.
- The fork avoids a second implementation of snapshot, retrieval, graph, prompt, restore, storage, API, or agent behavior.
- Cross-language packaging and schema versioning are deferred until the M5 boundary is designed in a separate ADR.
- Compatibility tests and additive configuration changes are required before extending established behavior.
