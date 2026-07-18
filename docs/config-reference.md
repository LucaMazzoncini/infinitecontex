# Config Reference

## Tool inspection

The additive `tools` section selects only supported built-in schema/policy version 1, whether `plan tool-check` persists decisions by default, and a bounded display limit:

```json
{"tools": {"registry_version": 1, "policy_version": 1, "persist_decisions": true, "display_limit": 100}}
```

G2 read/search limits are explicit bounded invocation inputs: at most 1,000 returned lines, 10,000 matches, 10,000 files, and 64 MiB scanned. Configuration cannot change effects, capability mappings, risk floors, sensitive-path rules, permanent restrictions, grants, approvals, or which definitions have handlers.

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
- `context_budget.warning_threshold_basis_points`

Important defaults in 0.2.0:

- `.infctx/**` is excluded by default
- `config/default.json` is tuned for Python repos
- `session` filtering uses the same exclude patterns as snapshot capture, plus `.infctx/**`

CLI note:

- `infctx setup` adds only missing `llm` and `chat` keys and preserves existing settings.
- Ollama defaults to `http://localhost:11434`; the initial preferred models are `qwen3.6:35b` and `qwen3-coder:30b`.
- Setup may create or reuse a digest-bound profile under `.infctx/model-profiles/`. Profiles are separate versioned artifacts, not inline configuration, and setup never calibrates them automatically.
- Context-budget inspection warns at 8750 basis points (87.5%) of the profile's maximum recommended input by default. Admission arithmetic and reserves remain profile-derived.
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

G3 safety limits are versioned code policy rather than user-tunable coefficients: 16 targets, one structured operation per target, 2 MiB per file, 8 MiB aggregate preimage/postimage data, and 128 KiB diff preview. No configuration option can bypass scope, sensitive-path, hash, approval, or rollback checks.
