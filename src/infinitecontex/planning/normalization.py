"""Canonical normalization, deterministic identifiers, and fingerprints."""

from __future__ import annotations

import hashlib
from typing import Any

import orjson

from infinitecontex.planning.models import PlanInput, PlanRevision, Task, TaskInput


def normalize_text(value: str) -> str:
    return " ".join(value.replace("\r\n", "\n").replace("\r", "\n").strip().split())


def sha256_payload(payload: Any) -> str:
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


def objective_hash(objective: str) -> str:
    return sha256_payload({"objective": normalize_text(objective)})


def generate_plan_id(plan: PlanInput) -> str:
    digest = sha256_payload(
        {
            "stable_plan_key": plan.stable_plan_key.casefold(),
            "objective": normalize_text(plan.objective),
            "repository_ref": normalize_text(plan.repository_ref).casefold(),
        }
    )
    return f"plan-{digest[:24]}"


def generate_task_id(plan_id: str, task_key: str) -> str:
    return f"task-{sha256_payload({'plan_id': plan_id, 'task_key': task_key.casefold()})[:24]}"


def task_fingerprint_payload(
    task: TaskInput, dependency_ids: tuple[str, ...], soft_ids: tuple[str, ...], parent_id: str | None
) -> dict[str, Any]:
    return {
        "task_key": task.task_key.casefold(),
        "title": normalize_text(task.title),
        "objective": normalize_text(task.objective),
        "description": task.description.replace("\r\n", "\n").replace("\r", "\n").strip(),
        "task_type": task.task_type,
        "status": task.status,
        "priority": task.priority,
        "dependency_ids": sorted(dependency_ids),
        "soft_dependency_ids": sorted(soft_ids),
        "parent_task_id": parent_id,
        "acceptance_criteria": sorted(
            (item.model_dump(mode="json") for item in task.acceptance_criteria), key=lambda item: item["criterion_id"]
        ),
        "required_evidence": sorted(
            (item.model_dump(mode="json") for item in task.required_evidence), key=lambda item: item["evidence_id"]
        ),
        "declared_inputs": sorted(
            (item.model_dump(mode="json") for item in task.declared_inputs), key=lambda item: item["name"]
        ),
        "expected_outputs": sorted(
            (item.model_dump(mode="json") for item in task.expected_outputs), key=lambda item: item["name"]
        ),
        "affected_scopes": sorted(task.affected_scopes),
        "forbidden_scopes": sorted(task.forbidden_scopes),
        "context_requirements": _sorted_context(task),
        "complexity": task.complexity,
        "context_class": task.context_class,
        "estimated_context_tokens": task.estimated_context_tokens,
        "requested_capabilities": sorted(task.requested_capabilities),
        "granted_capabilities": [],
        "risk_flags": sorted(task.risk_flags),
        "retry_policy": task.retry_policy.model_dump(mode="json"),
        "provenance": task.provenance,
        "metadata": task.metadata,
    }


def graph_fingerprint(tasks: tuple[Task, ...]) -> str:
    return sha256_payload(
        {
            "tasks": sorted(
                ({"task_id": task.task_id, "fingerprint": task.task_fingerprint} for task in tasks),
                key=lambda item: item["task_id"],
            ),
            "hard_edges": sorted((dependency, task.task_id) for task in tasks for dependency in task.dependency_ids),
            "soft_edges": sorted(
                (dependency, task.task_id) for task in tasks for dependency in task.soft_dependency_ids
            ),
            "parents": sorted((task.task_id, task.parent_task_id) for task in tasks if task.parent_task_id),
        }
    )


def semantic_plan_fingerprint(plan: PlanRevision) -> str:
    return sha256_payload(
        {
            "stable_plan_key": plan.stable_plan_key.casefold(),
            "title": normalize_text(plan.title),
            "objective_hash": plan.normalized_objective_hash,
            "status": plan.status,
            "planner_provenance": plan.planner_provenance,
            "parent_plan_id": plan.parent_plan_id,
            "originating_request_ref": plan.originating_request_ref,
            "repository_ref": normalize_text(plan.repository_ref),
            "target_branch": plan.target_branch,
            "model_profile_ref": plan.model_profile_ref,
            "planning_policy_version": plan.planning_policy_version,
            "graph_fingerprint": plan.graph_fingerprint,
            "warnings": sorted(plan.warnings),
            "metadata": plan.metadata,
        }
    )


def revision_fingerprint(plan: PlanRevision) -> str:
    return sha256_payload(
        {
            "semantic": semantic_plan_fingerprint(plan),
            "revision": plan.current_revision,
            "previous": plan.previous_revision_fingerprint,
            "reason": normalize_text(plan.revision_reason),
            "author": plan.revision_author,
            "changed_task_ids": sorted(plan.changed_task_ids),
            "structural_changes": sorted(plan.structural_changes),
        }
    )


def task_fingerprint_for_task(task: Task) -> str:
    context = task.context_requirements.model_dump(mode="json")
    for key, value in context.items():
        if isinstance(value, list):
            context[key] = sorted(value, key=_canonical_collection_key)
    payload = {
        "task_key": task.task_key.casefold(),
        "title": normalize_text(task.title),
        "objective": normalize_text(task.objective),
        "description": task.description.replace("\r\n", "\n").replace("\r", "\n").strip(),
        "task_type": task.task_type,
        "status": task.status,
        "priority": task.priority,
        "dependency_ids": sorted(task.dependency_ids),
        "soft_dependency_ids": sorted(task.soft_dependency_ids),
        "parent_task_id": task.parent_task_id,
        "acceptance_criteria": sorted(
            (item.model_dump(mode="json") for item in task.acceptance_criteria), key=lambda item: item["criterion_id"]
        ),
        "required_evidence": sorted(
            (item.model_dump(mode="json") for item in task.required_evidence), key=lambda item: item["evidence_id"]
        ),
        "declared_inputs": sorted(
            (item.model_dump(mode="json") for item in task.declared_inputs), key=lambda item: item["name"]
        ),
        "expected_outputs": sorted(
            (item.model_dump(mode="json") for item in task.expected_outputs), key=lambda item: item["name"]
        ),
        "affected_scopes": sorted(task.affected_scopes),
        "forbidden_scopes": sorted(task.forbidden_scopes),
        "context_requirements": context,
        "complexity": task.complexity,
        "context_class": task.context_class,
        "estimated_context_tokens": task.estimated_context_tokens,
        "requested_capabilities": sorted(task.requested_capabilities),
        "granted_capabilities": [],
        "risk_flags": sorted(task.risk_flags),
        "retry_policy": task.retry_policy.model_dump(mode="json"),
        "provenance": task.provenance,
        "metadata": task.metadata,
    }
    return sha256_payload(payload)


def verify_revision_integrity(plan: PlanRevision) -> None:
    from infinitecontex.planning.dag import DagAnalysis

    if plan.task_count != len(plan.tasks):
        raise ValueError("task count does not match persisted tasks")
    if len({task.task_id for task in plan.tasks}) != len(plan.tasks):
        raise ValueError("persisted task IDs are not unique")
    if len({task.task_key.casefold() for task in plan.tasks}) != len(plan.tasks):
        raise ValueError("persisted task keys are not unique")
    if plan.normalized_objective_hash != objective_hash(plan.objective):
        raise ValueError("objective hash does not match persisted objective")
    for task in plan.tasks:
        if task.plan_id != plan.plan_id:
            raise ValueError("task belongs to a different plan")
        if task.task_fingerprint != task_fingerprint_for_task(task):
            raise ValueError(f"task fingerprint mismatch for {task.task_id}")
    if plan.graph_fingerprint != graph_fingerprint(plan.tasks):
        raise ValueError("graph fingerprint does not match persisted tasks")
    if plan.revision_fingerprint != revision_fingerprint(plan):
        raise ValueError("revision fingerprint does not match persisted revision")
    issues, cycles = DagAnalysis(plan.tasks).validate()
    if issues or cycles:
        code = issues[0].code if issues else cycles[0].code
        raise ValueError(f"persisted task DAG violates {code}")


def _sorted_context(task: TaskInput) -> dict[str, Any]:
    payload = task.context_requirements.model_dump(mode="json")
    for key, value in payload.items():
        if isinstance(value, list):
            payload[key] = sorted(value, key=_canonical_collection_key)
    return payload


def _canonical_collection_key(value: Any) -> bytes:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)
