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
