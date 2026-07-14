# Config Reference

Merge precedence, highest first:

1. Environment overrides
2. Repo-local `.infctx/config.json`
3. Global `~/.config/infinitecontex/config.json`
4. Built-in defaults

Environment overrides currently supported:

- `INFCTX_TOKEN_BUDGET`

Key config fields:

- `project_name`
- `capture_max_files`
- `include_patterns`
- `exclude_patterns`
- `modes`
- `policies.token`
- `policies.summarization`
- `policies.privacy`
- `llm.provider`, `llm.base_url`, `llm.model`, `llm.fallback_models`, `llm.keep_alive`, `llm.request_timeout_seconds`
- `chat.auto_snapshot`, `chat.recent_turns`, `chat.stream`

Important defaults in 0.2.0:

- `.infctx/**` is excluded by default
- `config/default.json` is tuned for Python repos
- `session` filtering uses the same exclude patterns as snapshot capture, plus `.infctx/**`

CLI note:

- `infctx setup` adds only missing `llm` and `chat` keys and preserves existing settings.
- Ollama defaults to `http://localhost:11434`; the initial preferred models are `qwen3.6:35b` and `qwen3-coder:30b`.
- Setup may create or reuse a digest-bound profile under `.infctx/model-profiles/`. Profiles are separate versioned artifacts, not inline configuration, and setup never calibrates them automatically.
- `infctx config --set-file config/default.json` works from the repo root.
- If you also pass `--project-root`, the preset path is resolved relative to that project root when possible.

Example:

```json
{
  "project_name": "infinitecontex",
  "capture_max_files": 600,
  "include_patterns": ["**/*.py", "**/*.md", "pyproject.toml", "README.md"],
  "exclude_patterns": [
    ".git/**",
    ".infctx/**",
    ".venv/**",
    "node_modules/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    "**/*.pyc",
    "build/**",
    "dist/**"
  ]
}
```
