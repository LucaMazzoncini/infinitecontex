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
- `infctx plan validate --file PLAN.json [--allow-large-file] [--json]`
- `infctx plan import --file PLAN.json [--reason TEXT] [--allow-large-file] [--project-root PATH] [--json]`
- `infctx plan list [--project-root PATH] [--json]`
- `infctx plan show PLAN_ID [--revision N] [--project-root PATH] [--json]`
- `infctx plan history PLAN_ID [--project-root PATH] [--json]`
- `infctx plan graph PLAN_ID [--revision N] [--project-root PATH] [--json]`
- `infctx plan ready PLAN_ID [--project-root PATH] [--json]`
- `infctx plan resolve PLAN_ID [--task TASK_ID] [--revision N] [--repo PATH] [--project-root PATH] [--json]`
- `infctx plan context-fit PLAN_ID [--model MODEL] [--digest DIGEST] [--task TASK_ID] [--revision N] [--repo PATH] [--persist/--no-persist] [--project-root PATH] [--json]`
- `infctx plan context-analysis list PLAN_ID [--revision N] [--project-root PATH] [--json]`
- `infctx plan context-analysis show PLAN_ID ANALYSIS_ID [--revision N] [--repo PATH] [--project-root PATH] [--json]`
- `infctx plan split PLAN_ID --task TASK_ID --model MODEL --digest DIGEST [--repo PATH] [--project-root PATH] [--json]`
- `infctx plan split-proposal list PLAN_ID [--project-root PATH] [--json]`
- `infctx plan split-proposal show PLAN_ID PROPOSAL_ID [--project-root PATH] [--json]`
- `infctx plan approve-split PLAN_ID PROPOSAL_ID --actor TEXT [--reason TEXT] [--acknowledge-warnings] [--repo PATH] [--project-root PATH] [--json]`
- `infctx plan reject-split PLAN_ID PROPOSAL_ID --actor TEXT [--reason TEXT] [--project-root PATH] [--json]`
- `infctx plan approval list PLAN_ID [--project-root PATH] [--json]`
- `infctx plan approval show PLAN_ID APPROVAL_ID [--project-root PATH] [--json]`
- `infctx tool list [--category CATEGORY] [--capability CAPABILITY] [--status STATUS] [--json]`
- `infctx tool show TOOL_ID [--json]`
- `infctx tool registry [--json]`
- `infctx plan tool-check PLAN_ID --task TASK_ID [--tool TOOL_ID] [--revision N] [--repo PATH] [--persist/--no-persist] [--project-root PATH] [--json]`
- `infctx plan tool-decision list PLAN_ID [--task TASK_ID] [--revision N] [--repo PATH] [--project-root PATH] [--json]`
- `infctx plan tool-decision show PLAN_ID DECISION_ID [--revision N] [--repo PATH] [--project-root PATH] [--json]`
- `infctx repo files [--glob GLOB] [--suffix SUFFIX] [--directory-prefix PATH] [--offset N] [--limit N] [--repo PATH] [--json]`
- `infctx repo read PATH [--start-line N --end-line N] [--offset N] [--max-lines N] [--repo PATH] [--json]`
- `infctx repo search QUERY [--glob GLOB] [--ignore-case] [--whole-word] [--max-matches N] [--max-files N] [--max-bytes N] [--context-lines N] [--offset N] [--repo PATH] [--json]`
- `infctx plan tool-read PLAN_ID --task TASK_ID --path PATH [--revision N] [--start-line N --end-line N] [--repo PATH] [--json]`
- `infctx tool execution list [--limit N] [--project-root PATH] [--json]`
- `infctx tool execution show EXECUTION_ID [--project-root PATH] [--json]`
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
- `plan validate` strictly analyzes JSON without persistence. `plan import` writes only a completely valid immutable revision. `list`, `show`, `history`, `graph`, and `ready` are offline inspection commands; none contacts Ollama or executes a task. The default plan-file limit is 8 MiB, and no input is silently repaired or truncated.
- `plan resolve` inventories one local repository snapshot and reports every declared path and symbol outcome. `plan context-fit` freezes resolved source and reuses the existing estimator, operational budget calculator, ranker, and packer against an exact persisted digest-bound profile. Both commands are offline and read-only outside optional `.infctx` analysis/manifest persistence.
- `plan context-fit` exits with status 2 when any selected task does not pass. Human and JSON output include token totals, included/excluded candidates, deficit, warnings, errors, and rule-based remediation. It never invokes runtime admission, Ollama, a task runner, retrieval, or automatic splitting.
- `plan context-analysis list/show` inspect immutable compact analyses. `show` recomputes repository identity for staleness reporting; source content is not persisted by default.
- `plan split` calls only the deterministic bounded splitter. It requires an exact task, model, and digest, persists an immutable proposal, prints a compact source-free review by default, and returns the complete proposal with `--json`. Identical regeneration is idempotent. Proposal creation, listing, and showing never revise a plan or record approval.
- `plan approve-split` is the only split command that can apply a proposal. It requires an explicit nonblank human actor and optionally records a reason. Proposals with warnings additionally require `--acknowledge-warnings`. Before creating exactly the next revision it revalidates proposal integrity, current revision and graph, source task, repository snapshot, profile/digest, source and leaf analyses, complete contract coverage, every leaf fit, and final DAG validity. Any mismatch fails closed with a regeneration instruction.
- `plan reject-split` records an immutable rejection without revising the plan. A proposal accepts only one decision; duplicate approval or rejection attempts fail. `plan approval list/show` are inspection only.
- `tool list/show/registry` inspect the versioned built-in data registry. Definitions contain strict schemas, effects, capabilities, scopes, risk, and predicted approval requirements, but never a handler. Registration and export are deterministic and do not load plugins.
- `plan tool-check` evaluates one task against one or every registered definition, optionally persists compact decisions, and never executes or approves a tool. Human output leads with `Execution available now: NO`; JSON contains complete reason and scope records. Task capability requests remain requests and the granted-capability list is always empty.
- `plan tool-decision list/show` report current staleness without deleting or reusing old records. Plan, task, repository, analysis, profile, definition, registry, policy, or risk-rule changes make the linked record stale.
- `repo files/read/search` execute only the five G2 read-only handlers through exact fingerprint binding and invocation-scoped snapshot grants. Reads are UTF-8/UTF-8-BOM and bounded; partial results include continuation. Search is literal only, deterministic, and bounded by matches, files, and bytes. Inventory exclusions, unsafe links, internal state, and sensitive paths fail closed.
- `plan tool-read` is an explicitly requested task-bound read. It additionally requires the exact current revision/task, fresh passing context analysis, requested repository-read capability, passing structural policy, and a path within declared non-forbidden scopes. It creates no persistent grant.
- `tool execution list/show` inspect compact records containing request/content hashes, linkage, counts, and safe status codes. Records never contain source, snippets, secrets, or full queries.
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

## G3 mutation workflow

- `infctx repo patch validate --file FILE [--repo PATH] [--json]`
- `infctx plan mutation propose PLAN_ID --task TASK_ID --file FILE [--revision N] [--repo PATH] [--project-root PATH] [--json]`
- `infctx plan mutation list|show|approve|reject|apply ...`
- `infctx tool mutation list|show ...`

Validation is non-persisting. Proposal and decision persistence cannot modify repository targets. Apply is task-bound and requires the exact approved proposal. JSON exposes full structured operations; the human view leads with the bounded unified preview.
