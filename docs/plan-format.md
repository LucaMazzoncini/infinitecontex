# Strict Plan Format

Plans are UTF-8 JSON objects validated without Ollama or network access. Unknown fields, duplicate object keys, Markdown fences, and semantic repair are rejected. The complete synthetic example is [minimal-plan.json](../examples/plans/minimal-plan.json).

## Minimal plan

```json
{
  "schema_version": 1,
  "stable_plan_key": "example",
  "title": "Inspect a change",
  "objective": "Inspect a synthetic change deterministically.",
  "planner_provenance": "human_authored",
  "repository_ref": "local-repository",
  "tasks": [{
    "task_key": "inspect",
    "title": "Inspect",
    "objective": "Inspect the declared inputs.",
    "task_type": "analysis",
    "status": "ready",
    "provenance": "human_authored"
  }]
}
```

## Dependency example

The second task refers to the first by stable task key. IDs are generated deterministically.

```json
{
  "schema_version": 1,
  "stable_plan_key": "dependency-example",
  "title": "Validate after design",
  "objective": "Represent two dependent declarations.",
  "planner_provenance": "human_authored",
  "repository_ref": "local-repository",
  "tasks": [
    {"task_key": "design", "title": "Design", "objective": "Define the contract.", "task_type": "design", "status": "completed", "provenance": "human_authored"},
    {"task_key": "validate", "title": "Validate", "objective": "Validate the contract.", "task_type": "validation", "status": "ready", "dependency_keys": ["design"], "provenance": "human_authored"}
  ]
}
```

## Invalid cycle

This is rejected with a canonical cycle path; neither task is persisted.

```json
{
  "schema_version": 1,
  "stable_plan_key": "cycle-example",
  "title": "Invalid cyclic plan",
  "objective": "Demonstrate strict cycle rejection.",
  "planner_provenance": "human_authored",
  "repository_ref": "local-repository",
  "tasks": [
    {"task_key": "a", "title": "A", "objective": "Wait for B.", "task_type": "analysis", "dependency_keys": ["b"], "provenance": "human_authored"},
    {"task_key": "b", "title": "B", "objective": "Wait for A.", "task_type": "analysis", "dependency_keys": ["a"], "provenance": "human_authored"}
  ]
}
```

## Revisions

Reimport the same `plan_id` with a semantic change and a reason:

```powershell
infctx plan import --file plan-v2.json --reason "Add explicit validation task"
infctx plan history plan-0123456789abcdef01234567
infctx plan show plan-0123456789abcdef01234567 --revision 1 --json
```

The first import creates revision 1. A changed import creates exactly the next revision and references the previous fingerprint. An equivalent import is idempotent.

## Inspection

```powershell
infctx plan validate --file plan.json
infctx plan import --file plan.json --json
infctx plan list
infctx plan graph PLAN_ID
infctx plan ready PLAN_ID
```

These commands never execute tasks or grant requested capabilities.

## Structured task context

`context_requirements` accepts legacy string lists and typed `path_references` and `symbol_references`. Only explicit declarations become repository candidates.

```json
{
  "context_requirements": {
    "path_references": [
      {"value": "src\\infinitecontex\\cli.py", "kind": "exact_file", "requirement": "required"},
      {"value": "docs/*.md", "kind": "glob", "requirement": "optional"},
      {"value": "src/infinitecontex/task_context/service.py", "kind": "source_range", "line_start": 1, "line_end": 80}
    ],
    "symbol_references": [
      {"reference": "TaskContextService.context_fit", "language": "python", "symbol_name": "context_fit", "qualified_name": "TaskContextService.context_fit", "file_hint": "src/infinitecontex/task_context/service.py", "symbol_kind": "method"}
    ]
  }
}
```

An unqualified Python name with matches in multiple modules is `ambiguous`. A C# symbol is `unsupported_language` until a Roslyn adapter exists, although an exact `.cs` file remains usable. `../outside.py`, UNC/device paths, drive changes, and absolute paths outside the repository are rejected as unsafe rather than rewritten.

Required unresolved declarations prevent a passing fit. Optional unresolved declarations remain warnings. Context inspection never rewrites the plan:

```powershell
infctx plan resolve PLAN_ID --repo . --json
infctx plan context-fit PLAN_ID --model MODEL --digest sha256:EXACT_DIGEST
```

A successful result reports `fits_target`, `fits_with_warning`, or `fits_hard_limit`. Oversized mandatory context reports `split_required`; missing required files report `required_reference_unresolved`. Remediation is deterministic advice only.

## Deterministic split proposals

A persisted task with at least two structural required-context units can produce a bounded proposal:

~~~powershell
infctx plan split PLAN_ID --task TASK_ID --model MODEL --digest DIGEST
infctx plan split-proposal show PLAN_ID PROPOSAL_ID --json
~~~

The source revision remains current while the proposal is generated or inspected. The proposal contains the source fit, selected deterministic rule, direct-child and leaf counts, complete leaf fits, assigned scopes, contract mappings, dependency rewrites, requested capabilities, warnings, and the preview graph fingerprint.

An approved application transforms the source task identity into a completion barrier and creates deterministic child task identities beneath it. The barrier depends on every direct child, while downstream tasks retain the source identity as their dependency, so no dependent task crosses the split early. Recursive children preserve parent task IDs and split-depth metadata. Revision 1 remains immutable and revision 2 is exactly the approved proposed graph.

~~~powershell
infctx plan approve-split PLAN_ID PROPOSAL_ID --actor "User" --reason "Reviewed" --acknowledge-warnings
infctx plan history PLAN_ID
~~~

Rejection records a decision but leaves the source revision untouched. Capabilities requested by child tasks remain requests; splitting grants none.
