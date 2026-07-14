# Current Repository Audit

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
