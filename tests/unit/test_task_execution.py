from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_evidence_review import _ready
from typer.testing import CliRunner

from infinitecontex.cli import app
from infinitecontex.planning.store import PlanStore
from infinitecontex.storage.layout import build_layout
from infinitecontex.task_execution.errors import ActionRejected, AuthorizationDenied
from infinitecontex.task_execution.models import (
    ActionKind,
    ActionRequest,
    AuthorizationDecision,
    AuthorizationSpecification,
    CallerType,
    GrantState,
)
from infinitecontex.task_execution_cli import _service
from infinitecontex.validation_cli import _service as validation_service

NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)


def _validation_setup(tmp_path: Path):
    root, plan_id, task_id, _ = _ready(tmp_path, task_status="awaiting_review")
    validation = validation_service(root)
    validation_proposal = validation.store.list_proposals(plan_id)[0]
    service = _service(root)
    definition = service.registry.get_by_name_version("execution.run-tests", "1.0.0")
    spec = AuthorizationSpecification(
        purpose="Run exact approved tests",
        allowed_tools=("execution.run-tests",),
        allowed_actions=(ActionKind.RUN_VALIDATION_PROPOSAL,),
        permitted_validation_proposals=(validation_proposal.proposal_id,),
        maximum_total_actions=1,
        maximum_actions_per_tool={"execution.run-tests": 1},
        maximum_validation_actions=1,
        requested_actor="Human",
    )
    return root, plan_id, task_id, service, definition, validation_proposal, spec


def _active(tmp_path: Path):
    root, plan_id, task_id, service, definition, validation_proposal, spec = _validation_setup(tmp_path)
    before = PlanStore(build_layout(root).plans).load_current(plan_id)
    proposal = service.propose(root, plan_id, task_id, spec)
    assert PlanStore(build_layout(root).plans).load_current(plan_id) == before
    approval = service.decide(plan_id, proposal.proposal_id, "Human", AuthorizationDecision.APPROVED, reason="Reviewed")
    assert PlanStore(build_layout(root).plans).load_current(plan_id) == before
    grant = service.activate(root, plan_id, proposal.proposal_id)
    session = service.start_session(plan_id, grant.grant_id)
    assert PlanStore(build_layout(root).plans).load_current(plan_id) == before
    return root, plan_id, task_id, service, definition, validation_proposal, proposal, approval, grant, session


def test_proposal_approval_activation_are_deterministic_and_do_not_execute(tmp_path: Path) -> None:
    root, plan_id, task_id, service, _, _, spec = _validation_setup(tmp_path)
    calls: list[str] = []
    service._dispatch = lambda *_args: calls.append("called")  # type: ignore[method-assign,assignment]
    first = service.propose(root, plan_id, task_id, spec)
    second = service.propose(root, plan_id, task_id, spec)
    assert first.semantic_fingerprint == second.semantic_fingerprint
    service.decide(plan_id, first.proposal_id, "Human", AuthorizationDecision.APPROVED, reason="Reviewed")
    one = service.activate(root, plan_id, first.proposal_id)
    two = service.activate(root, plan_id, first.proposal_id)
    service.start_session(plan_id, one.grant_id)
    assert one == two and calls == []
    assert PlanStore(build_layout(root).plans).load_current(plan_id).tasks[0].granted_capabilities == ()


def test_explicit_action_consumes_once_and_duplicate_is_idempotent(tmp_path: Path) -> None:
    root, plan_id, task_id, service, definition, validation, _, _, grant, session = _active(tmp_path)
    request = ActionRequest(
        action_request_id="action-validation-1",
        grant_id=grant.grant_id,
        grant_fingerprint=grant.grant_fingerprint,
        plan_id=plan_id,
        plan_revision=grant.plan_revision,
        task_id=task_id,
        task_fingerprint=grant.task_fingerprint,
        tool_id=definition.tool_id,
        tool_version=definition.tool_version,
        action=ActionKind.RUN_VALIDATION_PROPOSAL,
        input={"proposal_id": validation.proposal_id},
        requested_output_bytes=1024,
        created_at=NOW,
    )
    first = service.run(root, plan_id, session.session_id, request)
    second = service.run(root, plan_id, session.session_id, request)
    state = service.store.load_state(plan_id, grant.grant_id)
    assert first == second and first.allowance_consumed
    assert state.remaining_total_actions == 0 and state.state == GrantState.EXHAUSTED


def test_denied_action_never_dispatches_and_consumes_nothing(tmp_path: Path) -> None:
    root, plan_id, task_id, service, definition, validation, _, _, grant, session = _active(tmp_path)
    calls: list[str] = []
    service._dispatch = lambda *_args: calls.append("called")  # type: ignore[method-assign,assignment]
    request = ActionRequest(
        action_request_id="action-denied",
        grant_id=grant.grant_id,
        grant_fingerprint=grant.grant_fingerprint,
        plan_id=plan_id,
        plan_revision=grant.plan_revision,
        task_id=task_id,
        task_fingerprint=grant.task_fingerprint,
        tool_id=definition.tool_id,
        tool_version=definition.tool_version,
        action=ActionKind.REPO_READ,
        input={"path": "tests/test_review.py", "proposal_id": validation.proposal_id},
        caller_type=CallerType.FUTURE_AGENT,
        created_at=NOW,
    )
    with pytest.raises(ActionRejected):
        service.run(root, plan_id, session.session_id, request)
    assert calls == [] and service.store.load_state(plan_id, grant.grant_id).remaining_total_actions == 1


def test_revocation_and_closed_session_fail_closed(tmp_path: Path) -> None:
    _, plan_id, _, service, _, _, _, _, grant, session = _active(tmp_path)
    first = service.revoke(plan_id, grant.grant_id, "Human", "Stop")
    second = service.revoke(plan_id, grant.grant_id, "Human", "Stop")
    assert first == second
    assert service.store.load_state(plan_id, grant.grant_id).state == GrantState.REVOKED
    assert service.close_session(plan_id, session.session_id).state.value == "revoked"


def test_unknown_tool_wildcards_and_ineligible_status_rejected(tmp_path: Path) -> None:
    root, plan_id, task_id, service, _, _, spec = _validation_setup(tmp_path)
    with pytest.raises(ValueError):
        AuthorizationSpecification(**{**spec.model_dump(), "allowed_tools": ("*",)})
    with pytest.raises(AuthorizationDenied):
        service.propose(
            root,
            plan_id,
            task_id,
            spec.model_copy(
                update={
                    "allowed_tools": ("execution.run-bounded-shell",),
                    "maximum_actions_per_tool": {"execution.run-bounded-shell": 1},
                }
            ),
        )


def test_cli_authorization_preview_json_lists_and_module_entrypoint(tmp_path: Path) -> None:
    root, plan_id, task_id, _, _, validation, spec = _validation_setup(tmp_path)
    spec_file = tmp_path / "authorization.json"
    spec_file.write_text(json.dumps(spec.model_dump(mode="json")), encoding="utf-8")
    runner = CliRunner()
    proposed = runner.invoke(
        app,
        [
            "plan",
            "execution",
            "authorize",
            plan_id,
            "--task",
            task_id,
            "--file",
            str(spec_file),
            "--repo",
            str(root),
            "--project-root",
            str(root),
        ],
    )
    assert proposed.exit_code == 0, proposed.stdout
    assert "Task status grants execution: NO" in proposed.stdout
    listing = runner.invoke(
        app, ["plan", "execution-authorization", "list", plan_id, "--project-root", str(root), "--json"]
    )
    assert listing.exit_code == 0 and validation.proposal_id in listing.stdout
    module = runner.invoke(app, ["--version"])
    assert module.exit_code == 0


def test_scale_maximum_allowance_and_reversed_input_fingerprint(tmp_path: Path) -> None:
    _, _, _, _, _, _, spec = _validation_setup(tmp_path)
    maximum = spec.model_copy(
        update={"maximum_total_actions": 1000, "maximum_actions_per_tool": {"execution.run-tests": 1000}}
    )
    assert maximum.maximum_total_actions == 1000
    assert spec.model_copy(update={"allowed_actions": tuple(reversed(spec.allowed_actions))}) == spec
