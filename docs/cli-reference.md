# CLI Reference

Global:

- `infctx --version`
- `infctx --help`
- `infctx --project-root PATH COMMAND ...`

Structured workflow:

- `infctx setup [--project-root PATH] [--check-only] [--yes] [--json]`
- `infctx chat [--project-root PATH] [--model NAME] [--no-snapshot]`
- `infctx model profile list [--project-root PATH] [--json]`
- `infctx model profile show MODEL [--digest DIGEST] [--project-root PATH] [--json]`
- `infctx model profile create MODEL [--project-root PATH] [--json]`
- `infctx init [--project-root PATH] [--json]`
- `infctx session [--goal TEXT] [--project-root PATH] [--debounce-ms INT] [--min-interval-sec INT] [--once] [--json]`
- `infctx watch [--goal TEXT] [--project-root PATH] [--debounce-ms INT] [--min-interval-sec INT]`
- `infctx status [--project-root PATH] [--json]`
- `infctx snapshot [--goal TEXT] [--project-root PATH] [--json]`
- `infctx snapshots [--limit INT] [--project-root PATH] [--json]`
- `infctx show-snapshot [--snapshot-id ID] [--project-root PATH] [--json]`
- `infctx compare-snapshots [--from-snapshot ID] [--to-snapshot ID] [--project-root PATH] [--json]`
- `infctx restore [--snapshot-id ID] [--project-root PATH] [--json]`
- `infctx prompt [--mode MODE] [--token-budget INT] [--snapshot-id ID] [--project-root PATH]`

Context curation:

- `infctx note --summary TEXT --rationale TEXT [--alternative TEXT] [--impact TEXT] [--tag TEXT] [--project-root PATH]`
- `infctx pin --path TEXT [--note TEXT] [--project-root PATH]`
- `infctx pins [--project-root PATH] [--json]`
- `infctx unpin --path TEXT [--project-root PATH]`
- `infctx ingest-chat [--file PATH | --auto] [--project-root PATH] [--json]`
- `infctx decisions [--limit INT] [--project-root PATH] [--json]`
- `infctx search --query TEXT [--limit INT] [--project-root PATH] [--json]`
- `infctx diff-summary [--project-root PATH] [--json]`

Project maintenance:

- `infctx doctor [--project-root PATH] [--json]`
- `infctx config [--set-file PATH] [--project-root PATH] [--json]`
- `infctx export --output PATH [--project-root PATH]`
- `infctx import --archive PATH [--project-root PATH]`
- `infctx cleanup [--keep INT] [--project-root PATH] [--yes]`
- `infctx setup-agent AGENT [--project-root PATH]`

Notes:

- `setup` probes the configured local Ollama service and installed models. It never downloads a model. `--check-only` performs no writes; `--yes` accepts additive `.infctx` initialization/configuration without prompting.
- `chat` is read-only in this first slice. It streams Ollama responses, keeps a bounded in-process history, attempts a safe startup snapshot, and supports `/help`, `/status`, `/context`, and `/quit`.
- `/context` reports only bounded-history state for now; the deterministic context-budget governor is not implemented until M2.
- Model profile creation inspects an already installed Ollama model and writes an uncalibrated conservative profile. It does not download or benchmark models.
- Profile lookup is digest-specific. `show` requires `--digest` when multiple builds of the same model name are persisted.
- `session` is the preferred live workflow command.
- `watch` is a compatibility alias for `session`.
- `session --json` is supported only together with `--once`.
- `compare-snapshots` defaults to the latest snapshot compared against the immediately previous one.
- `cleanup` requires `--yes` when it will delete old snapshots.
- `config --set-file` resolves relative preset paths against `--project-root` when provided.
- For reliable intent capture, prefer `ingest-chat --file` over `ingest-chat --auto`.
