# Current Repository Audit

## G2 safe repository read and literal search execution

G2 adds immutable invocation, read-grant, result, and execution-record contracts; exact definition-to-handler bindings; centralized fail-closed admission; five bounded inventory/read/search handlers; sensitive-path policy; compact persistence; safe events; and direct-human plus task-bound orchestration under the existing `tools/` package. It reuses the M3 E2 inventory, path resolver, scope matching, hashing, binary detection, symlink/reparse checks, and file-size policy rather than creating another scanner.

Only `repository.list-inventory`, `repository.read-file`, `repository.read-source-range`, `repository.search-paths`, and `repository.search-literal` have handlers. The CLI adds `repo files/read/search`, `plan tool-read`, and `tool execution list/show`. Literal queries are never regex. Every call has an invocation-scoped snapshot/tool/scope grant; task-bound reads additionally require exact fresh plan/task/context/policy/scope state. Records contain hashes and counts but no content, snippets, secrets, or full queries.

The local authoritative equivalents are `docs/infinitecontext-next/11_AGENT_RUNTIME_AND_TOOLS.md` for tool execution, `09_RETRIEVAL_AND_CONTEXT_PACKS.md` plus ADR 0008 and `task_context/` for repository inventory and path resolution, `15_SECURITY_GIT_AND_APPROVALS.md` for guardrails, and `17_CLI_AND_API_CONTRACT.md`, `18_OBSERVABILITY_AND_DIAGNOSTICS.md`, `19_TEST_STRATEGY.md`, and `20_IMPLEMENTATION_ROADMAP.md` for CLI, observability, tests, and sequencing. The supplied specification set has no separate repository-scanning or path-resolution document, so the implemented M3 E2 components remain the code-level authority.

Pre-change G2 baseline on 2026-07-16: Ruff format reported 164 files already formatted; Ruff lint passed; strict mypy passed for 118 source files; all 245 tests passed in 44.03 seconds with 91% total coverage; `uv build` and `git diff --check` passed.

Post-change G2 gates on 2026-07-18: Ruff format reported 174 files already formatted; Ruff lint passed; strict mypy passed for 126 source files; all 260 tests passed in 89.09 seconds with 91% total coverage; `uv build` produced both distribution artifacts; and `git diff --check` passed. Focused tests cover exact bindings, rejected-dispatch isolation, direct and task-bound CLI paths, sensitive and stale denials, deterministic continuation, and bounded 10,000-file listing/search.

Write, patch, shell, subprocess, Git mutation, network, dependency installation, model/Ollama, regex, semantic search, embeddings, and future-agent execution remain unavailable.

## G1 versioned tool registry and deterministic capability policies

G1 adds a focused `tools/` package for immutable definitions, strict input/output schemas, SHA-256 identities, data-only registration, capability reuse, explicit effect/scope declarations, risk floors, predicted approval classes, deterministic policy decisions, compact persistence, and offline orchestration. The built-in registry contains conservative read, mutation, execution, Git, and restricted candidates; every entry is declared-only or unavailable and every `execution_handler` is null.

The planning capability enum remains authoritative and now additively includes artifact writes, plan mutation, and task-status mutation. Task grants remain structurally empty. Repository scope checks reuse `task_context.scopes` normalization/matching. The CLI adds `tool list/show/registry`, `plan tool-check`, and `plan tool-decision list/show`. These paths construct no Ollama client, make no network request, load no plugin, execute no tool or shell command, grant no capability, approve nothing, and modify only optional `.infctx` decision records.

Pre-change G1 baseline on 2026-07-15: Ruff format reported 149 files already formatted; Ruff lint passed; strict mypy passed for 105 source files; all 228 tests passed in 39.29 seconds with 91% total coverage; and `uv build` produced the sdist and wheel.

Post-change G1 gates on 2026-07-15: Ruff format reported 164 files already formatted; Ruff lint passed; strict mypy passed for 118 source files; all 245 tests passed with 91% total coverage and 90% coverage for `cli.py`; `uv build` produced the sdist and wheel; and `git diff --check` passed. Dedicated tests additionally validate 1,000 definitions, 1,000 task/tool decisions, and compatibility with a 10,000-task plan without timing assertions.

## M3 F6 compact split proposal and approval CLI

F6 exposes the existing `task_splitting/` services through additive `plan split`, `plan split-proposal list/show`, `plan approve-split`, `plan reject-split`, and `plan approval list/show` commands. The CLI constructs only the existing planning, task-context, profile-store, and split services; it does not construct Ollama, use network access, execute tasks, edit repository source, grant capabilities, or change a plan during proposal creation or inspection.

The compact proposal view includes source revision/task and fit, rationale, selected rule, direct and leaf counts, depth, each leaf fit and token use, child scopes, criterion/evidence coverage, dependency rewrites, requested capabilities, warnings/errors, all-leaves-fit status, and the resulting task-count/graph preview. `--json` returns the complete persisted contract. Approval requires exact plan/proposal IDs and a nonblank human actor, binds the verified proposal fingerprint, rejects duplicate decisions, and revalidates revision, graph, source task, snapshot, exact profile/digest, source/leaf analyses, coverage, fit, and final DAG before one new immutable revision is written. Rejection persists a decision without revising the plan.

Pre-change F6 baseline on 2026-07-15: `ruff format --check .` exited 1 and identified five already committed files (`task_context/service.py` plus four `task_splitting/` files); `ruff check .` exited 0; strict mypy exited 0 for 105 source files; all 222 tests passed with 91% total coverage; and `uv build` exited 0. F6 includes only the mechanical formatter output needed to make the required format gate pass.

Post-change F6 gates on 2026-07-15: `ruff format --check .`, `ruff check .`, strict mypy for 105 source files, all 228 tests with 91% total coverage, `uv build`, and `git diff --check` each exited 0. The dedicated three-test CLI smoke suite covers human/JSON proposal output, list/show, approval, rejection, exactly one approved revision, historical preservation, stale-proposal refusal, no source modification, offline Ollama-construction refusal, and the module entrypoint.

## M3 E2 deterministic task-context reconciliation

The requested repository-scanning material maps locally to `03_REPOSITORY_SCANNING.md`; context budgeting and packing map to `08_CONTEXT_BUDGET_GOVERNOR.md` and `09_RETRIEVAL_AND_CONTEXT_PACKS.md`. There is no standalone knowledge-graph specification, so `06_DOMAIN_MODEL.md` plus the existing `graph/` package remain authoritative. Token estimation is governed by ADR 0002, deterministic budget calculation by ADR 0003, ranking/packing by ADR 0004, runtime admission by ADR 0006, and strict plans by ADR 0007.

M3 E2 adds `task_context/` for repository snapshots, bounded inventory, safe path resolution, pluggable symbol resolution, Python AST indexing, scope validation, explicit candidate construction, fit orchestration, typed errors, and analysis persistence. It reuses `planning/`, `model_profiles/`, `context_budget/`, `context_packing/`, `context_admission/` policy semantics, manifest storage, and atomic JSON writes. CLI additions are `plan resolve`, `plan context-fit`, and `plan context-analysis list/show`. No LLM, Ollama access, network operation, source import, execution, retrieval, editing, capability grant, or automatic task split is part of this slice.

## M3 E1 strict-planning reconciliation

The requested task-decomposition specification maps locally to `10_TASK_PLANNING_AND_DECOMPOSITION.md`; no standalone task-persistence document exists. Domain and storage authority is distributed across `06_DOMAIN_MODEL.md`, `05_ARCHITECTURE_EVOLUTION.md`, `storage/layout.py`, and the established atomic JSON stores. M3 E1 therefore adds an independent `planning/` package and `.infctx/plans/` layout instead of placing execution semantics into the existing generic knowledge graph.

Existing captured tasks are `IntentContext.active_tasks: list[str]` populated by snapshot/chat ingestion. They remain historical intent signals with unchanged behavior. There is no adapter, automatic migration, or reinterpretation into strict DAG tasks in this slice.

## M2 E4 additive runtime update

The repository now includes `context_admission/`, which reuses the persisted profile store, context estimator/calculator, manifest store, and Ollama client abstraction. It adds no second client and no repository retrieval. `chat` is the sole integrated model-call path and is now fail-closed through an immutable admitted payload. The roadmap's broad runtime specification mentions later tool execution; this slice deliberately implements only chat admission because editing, autonomous tools, retrieval, and task execution remain explicitly out of scope.

Audit date: 2026-07-14

## Repository identity and state

- Package/version: `infinitecontex` 0.3.1, Python 3.11+, MIT.
- Branch: `main`, tracking `origin/main`.
- Remote: `https://github.com/LucaMazzoncini/infinitecontex.git` for fetch and push.
- Initial tracked working tree: clean. The supplied fork specifications (`AGENTS.md`, `BOOTSTRAP_MANIFEST.md`, `CODEX_START_PROMPT.md`, `INFINITE_CONTEXT_NEXT.md`, `USER_QUICKSTART_IT.md`, and `docs/infinitecontext-next/`) were untracked.
- Build backend: Hatchling. Console entry point: `infctx = infinitecontex.cli:app`.

## Relevant package tree

```text
src/infinitecontex/
  cli.py                 single Typer command registry and Rich rendering
  service.py             snapshot/memory application orchestration
  core/                  Pydantic models, config, policy, redaction, serde
  capture/               repository, Git, working set, terminal, chat ingestion
  storage/               .infctx layout, SQLite, export/import
  retrieval/             SQLite full-text retrieval
  graph/                 persisted context graph
  prompt/                restore prompt compilation
  restore/               snapshot restore validation
  decisions/             decision persistence
  events/                JSONL event logging
  doctor/                local diagnostics
  agent/                 existing read-only context interface
  api/                   in-process client surface
```

Storage uses `.infctx/` directories plus `metadata/state.db`. Schema creation is idempotent and currently covers snapshots, decisions, events, and pins. `metadata/manifest.json` has schema version 1. No general migration-version table exists.

## Existing CLI contract

Commands registered before this slice were: `init`, `snapshot`, `snapshots`, `show-snapshot`, `compare-snapshots`, `restore`, `setup-agent`, `status`, `note`, `pin`, `pins`, `unpin`, `ingest-chat`, `diff-summary`, `decisions`, `search`, `prompt`, `export`, `import`, `doctor`, `config`, `session`, `watch`, and `cleanup`. The global callback supports `--version` and `--project-root`. `watch` delegates to `session` for compatibility.

Compatibility inventory is primarily `tests/unit/test_cli.py`, with module entry-point and installed-client coverage in `tests/integration/`. It exercises parsing and representative output for all established workflows; additions must not rename or change them.

## Tests and quality baseline

Test layout: `tests/unit`, `tests/integration`, `tests/golden`, and `tests/performance`. The baseline collection executed 78 tests.

Exact pre-change results:

- `uv sync --extra dev`: exit 0. Resolved 38 packages, installed 37, and built local `infinitecontex==0.3.1`.
- `uv run ruff check .`: exit 0, `All checks passed!`.
- `uv run mypy src`: exit 0, `Success: no issues found in 42 source files`.
- `uv run pytest`: exit 1; `1 failed, 77 passed in 14.08s`, total coverage 95%. `tests/integration/test_module_entrypoint.py::test_module_entrypoint_version` hard-coded the POSIX path `.venv/bin/python` and failed on Windows with `FileNotFoundError: [WinError 2]`.
- `uv build`: exit 0. Built `dist\infinitecontex-0.3.1.tar.gz` and `dist\infinitecontex-0.3.1-py3-none-any.whl`.

The uv commands initially could not access the user cache under the filesystem sandbox; rerunning with explicitly approved cache access succeeded. That environmental denial was not a repository failure.

## Dependencies

Runtime: Typer, Pydantic, pydantic-settings, NetworkX, orjson, Rich, and Watchfiles. Development: pytest, pytest-cov, Ruff, mypy, types-networkx, and build. The Ollama first slice can use `urllib.request`, so no runtime dependency is required.

## Confirmed extension points

- Reuse `InfiniteContextService.init()` for idempotent `.infctx/` initialization.
- Reuse `InfiniteContextService.snapshot()` for safe chat-start memory capture and `status()` for established repository facts.
- Extend `AppConfig` additively; preserve JSON precedence and unknown fork settings.
- Add an `llm` provider protocol/adapter rather than coupling HTTP calls to Typer or the memory service.
- Add chat and setup application services called directly by CLI commands.
- Continue using Rich and Typer; do not add a daemon or full-screen UI.

## Specification conflicts and verified gaps

- The public baseline matches local version 0.3.1, but the local remote is this user's fork rather than upstream.
- The pre-existing module-entrypoint integration test was POSIX-only despite Windows being the target platform.
- Existing config supported only `INFCTX_TOKEN_BUDGET`; the proposed LLM/chat sections did not exist.
- No native Ollama transport, provider errors, setup command, or terminal chat existed.
- Existing `agent/interface.py` exposes memory context; it is not an autonomous runtime and must not be duplicated.
- Existing prompt token policy is a heuristic for restore artifacts, not the M2 hard per-model-call context governor. This slice must not represent it as that governor.
- No Roslyn, recursive task planning, context-pack manifests, editing tools, or approval state machine exists. These remain later milestones.

## M2 E1 specification-name reconciliation

The M2 E1 request named `09_CONTEXT_BUDGET_ENGINE.md`, `13_OLLAMA_INTEGRATION.md`, and `16_CONFIGURATION.md`. Those files do not exist in the local specification tree. The authoritative local equivalents used for implementation were `08_CONTEXT_BUDGET_GOVERNOR.md`, `07_OLLAMA_AND_MODEL_SELECTION.md`, and `16_CONFIGURATION_DEFAULTS.md`. No missing document was fabricated, and local repository naming took precedence as required by `00_READ_FIRST.md`.

## M2 context-packing specification reconciliation

The context-packing slice requested a local knowledge-graph specification, but the local specification tree contains no standalone knowledge-graph document. The authoritative guidance is distributed across `06_DOMAIN_MODEL.md`, `09_RETRIEVAL_AND_CONTEXT_PACKS.md`, and the existing `src/infinitecontex/graph/store.py`. The implementation consumes explicit candidates only and does not expand or duplicate the existing graph/retrieval engines.

## G3 approved transactional text mutations

G3 reuses G1 definitions/policy and G2 inventory, path resolution, hashing, sensitive filtering, snapshots, and atomic persistence. The local specification set has no separate task-execution or repository-scanning file; authoritative equivalents are roadmap/task planning, agent runtime, retrieval, security, ADR 0008, and the implemented `planning`, `task_context`, and `tools` packages.

Only `repository.apply-source-patch`, `repository.write-test`, and `repository.write-documentation` gain mutation-workflow availability. Exact line-range replacement and new UTF-8 file creation are proposal-bound, human-approved, rollback-protected, and offline. Delete, rename, configuration, binary, shell, subprocess, Git, network, Ollama/LLM, chat approval, task transitions, and autonomous execution remain disabled.

Final G3 gates on 2026-07-18: Ruff format reported 181 files formatted; Ruff lint passed; strict mypy passed for 132 source files; all 267 tests passed in 91.58 seconds with 91% coverage; `uv build` produced sdist and wheel; and `git diff --check` passed. Focused tests prove non-mutating proposal/approval, exact approved apply, stale/unapproved/protected denial, deterministic fingerprinting, 16-target bounds, rollback after a partial two-target apply, compact records, CLI/module workflows, and runtime subprocess/network isolation.
