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
- `infctx model budget show MODEL [--digest DIGEST] [--project-root PATH] [--json]`
- `infctx model budget estimate MODEL (--text TEXT | --text-file PATH) [--digest DIGEST] [--output-tokens INT] [--tool-result-tokens INT] [--project-root PATH] [--json]`
- `infctx model budget estimate-text (--text TEXT | --text-file PATH) [--allow-large-file] [--json]`
- `infctx context pack --model MODEL --candidate-file PATH [--digest DIGEST] [--already-reserved-tokens INT] [--persist/--no-persist] [--allow-large-file] [--project-root PATH] [--json]`
- `infctx context admit --request-file PATH [--allow-large-file] [--project-root PATH] [--json]`
- `infctx context admission list [--project-root PATH] [--json]`
- `infctx context admission show ADMISSION_ID [--project-root PATH] [--json]`
- `infctx context manifest list [--project-root PATH] [--json]`
- `infctx context manifest show MANIFEST_ID [--project-root PATH] [--json]`
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
- `model budget estimate-text` reports the versioned conservative heuristic and legacy byte estimate without loading a profile or contacting Ollama. File input is limited to 8 MiB unless `--allow-large-file` is explicit; input is never truncated.
- `context pack` ranks caller-supplied candidates and creates an inspection-only budget manifest. It uses an exact persisted profile, never contacts Ollama, and persists under `.infctx/context-manifests/` unless `--no-persist` is supplied. Candidate files are limited to 8 MiB unless `--allow-large-file` is explicit.
- `context admit` validates a frozen request against an exact persisted profile and manifest without contacting Ollama. Request JSON follows `AdmissionRequest`: exact provider/model/digest/profile/manifest identity, typed `system_instructions` and `current_user_request`, optional typed section arrays, allowances, and a timezone-aware `calculated_at`. Input is limited to 8 MiB unless explicitly allowed.
- `context admission list/show` inspect compact records under `.infctx/context-admissions/`; prompt content is not persisted.
- `chat` remains read-only, but every turn now creates a deterministic manifest and passes through the fail-closed runtime admission gate before Ollama streaming. History is bounded and optional; system instructions and the current user request are mandatory and are never silently truncated. `/context` reports the active gate, digest, profile, latest manifest/admission, token budget, and reserves.
- `/context` reports bounded-history state; deterministic budget calculations remain a separate read-only inspection command and are not runtime enforcement.
- Model profile creation inspects an already installed Ollama model and writes an uncalibrated conservative profile. It does not download or benchmark models.

### Candidate JSON

The file is either an array or an object with a `candidates` array. Inline content is estimated; a referenced candidate without content must provide an explicit token count.

```json
{
  "candidates": [
    {
      "candidate_id": "task",
      "category": "current_task",
      "label": "Current task",
      "content": "Implement deterministic packing.",
      "mandatory": true,
      "retention_priority": 100
    },
    {
      "candidate_id": "target",
      "category": "source_code_excerpt",
      "label": "Target source",
      "content": "def pack(): ...",
      "source_path": "src/packer.py",
      "line_start": 1,
      "line_end": 20,
      "direct_request_match": true
    }
  ]
}
```
- Profile lookup is digest-specific. `show` requires `--digest` when multiple builds of the same model name are persisted.
- Budget commands use only persisted verified profiles and never contact Ollama. Results are deterministic inspection decisions; runtime model-call enforcement is not implemented yet.
- Text estimation normalizes line endings and conservatively counts normalized UTF-8 bytes. Supply measured counts through the Python service when exact tokenizer results are available.
- `session` is the preferred live workflow command.
- `watch` is a compatibility alias for `session`.
- `session --json` is supported only together with `--once`.
- `compare-snapshots` defaults to the latest snapshot compared against the immediately previous one.
- `cleanup` requires `--yes` when it will delete old snapshots.
- `config --set-file` resolves relative preset paths against `--project-root` when provided.
- For reliable intent capture, prefer `ingest-chat --file` over `ingest-chat --auto`.
