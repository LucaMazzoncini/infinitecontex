from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from infinitecontex.cli import app
from infinitecontex.llm.models import InstalledModel, ModelDetails
from infinitecontex.model_profiles.service import ModelProfileService
from infinitecontex.model_profiles.store import ModelProfileStore
from infinitecontex.mutation_cli import _service
from infinitecontex.storage.layout import build_layout
from infinitecontex.tools.mutation_errors import MutationAdmissionError
from infinitecontex.tools.mutation_models import (
    CreateTextFile,
    FileKind,
    MutationDecision,
    MutationRequest,
    MutationStatus,
    ReplaceLineRange,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class FakeIdentityClient:
    def list_models(self) -> list[InstalledModel]:
        return [InstalledModel(name="demo:latest", digest="sha256:test")]

    def show_model(self, name: str) -> ModelDetails:
        return ModelDetails(name=name, parameters="num_ctx 8192", model_info={"demo.context_length": 999999})


def _profile(root: Path) -> None:
    ModelProfileService(
        FakeIdentityClient(),  # type: ignore[arg-type]
        ModelProfileStore(build_layout(root).model_profiles),
        clock=lambda: datetime(2026, 7, 18, tzinfo=UTC),
    ).create_or_reuse("demo:latest")


def _ready_repo(tmp_path: Path, *, files: int = 2) -> tuple[Path, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    for index in range(files):
        (root / "src" / f"file{index}.py").write_bytes(f"old-{index}\n".encode())
    _profile(root)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(
        json.dumps(
            {
                "stable_plan_key": "g3-mutation",
                "title": "G3 mutation",
                "objective": "Apply exact structured source changes.",
                "status": "active",
                "planner_provenance": "human_authored",
                "repository_ref": "test-repo",
                "tasks": [
                    {
                        "task_key": "edit",
                        "title": "Edit sources",
                        "objective": "Edit exact source files.",
                        "task_type": "implementation",
                        "status": "ready",
                        "affected_scopes": ["src/**"],
                        "forbidden_scopes": ["src/forbidden/**"],
                        "requested_capabilities": ["write_source_files"],
                        "context_requirements": {"required_files": ["src/file0.py"]},
                        "provenance": "human_authored",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    imported = runner.invoke(app, ["plan", "import", "--file", str(plan_file), "--project-root", str(root), "--json"])
    payload = json.loads(imported.stdout)
    plan_id, task_id = payload["plan"]["plan_id"], payload["plan"]["tasks"][0]["task_id"]
    fit = runner.invoke(
        app,
        [
            "plan",
            "context-fit",
            plan_id,
            "--task",
            task_id,
            "--model",
            "demo:latest",
            "--digest",
            "sha256:test",
            "--repo",
            str(root),
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert fit.exit_code == 0, fit.stdout
    return root, plan_id, task_id


def _replace(index: int) -> ReplaceLineRange:
    old, new = f"old-{index}\n", f"new-{index}\n"
    return ReplaceLineRange(
        path=f"src/file{index}.py",
        file_kind=FileKind.SOURCE,
        expected_preimage_sha256=_sha(old),
        start_line=1,
        end_line=1,
        expected_old_text=old,
        replacement_text=new,
        expected_postimage_sha256=_sha(new),
    )


def test_proposal_approval_apply_are_exact_deterministic_and_content_safe(tmp_path: Path) -> None:
    root, plan_id, task_id = _ready_repo(tmp_path)
    service = _service(root)
    before = {path.name: path.read_bytes() for path in (root / "src").glob("*.py")}
    request = MutationRequest(operations=(_replace(1), _replace(0)))
    proposal = service.propose(root, plan_id, task_id, request)
    repeated = service.propose(root, plan_id, task_id, MutationRequest(operations=tuple(reversed(request.operations))))
    assert proposal.semantic_fingerprint == repeated.semantic_fingerprint
    assert {path.name: path.read_bytes() for path in (root / "src").glob("*.py")} == before
    assert proposal.diff_preview.index("src/file0.py") < proposal.diff_preview.index("src/file1.py")
    approval = service.decide(plan_id, proposal.proposal_id, "Human", MutationDecision.APPROVED)
    assert approval.proposal_fingerprint == proposal.semantic_fingerprint
    assert {path.name: path.read_bytes() for path in (root / "src").glob("*.py")} == before
    record = service.apply(root, plan_id, proposal.proposal_id)
    assert record.status == MutationStatus.APPLIED
    assert (root / "src/file0.py").read_text() == "new-0\n"
    assert (root / "src/file1.py").read_text() == "new-1\n"
    serialized = service.store.serialize(record).decode()
    assert "new-0" not in serialized and "old-0" not in serialized and "diff" not in serialized


def test_unapproved_rejected_stale_and_protected_requests_fail_closed(tmp_path: Path) -> None:
    root, plan_id, task_id = _ready_repo(tmp_path)
    service = _service(root)
    proposal = service.propose(root, plan_id, task_id, MutationRequest(operations=(_replace(0),)))
    with pytest.raises(MutationAdmissionError, match="approval"):
        service.apply(root, plan_id, proposal.proposal_id)
    service.decide(plan_id, proposal.proposal_id, "Human", MutationDecision.REJECTED)
    with pytest.raises(MutationAdmissionError, match="approval"):
        service.apply(root, plan_id, proposal.proposal_id)
    assert (root / "src/file0.py").read_text() == "old-0\n"
    for path in ("../escape.py", ".git/config", ".infctx/state.json", ".env", ".venv/x.py"):
        request = MutationRequest(
            operations=(
                CreateTextFile(
                    path=path, file_kind=FileKind.SOURCE, content="x\n", expected_postimage_sha256=_sha("x\n")
                ),
            )
        )
        assert not service.validate_request(root, request).valid


def test_partial_failure_rolls_back_existing_and_new_files(tmp_path: Path) -> None:
    root, plan_id, task_id = _ready_repo(tmp_path)
    service = _service(root)
    create = CreateTextFile(
        path="src/new.py",
        file_kind=FileKind.SOURCE,
        content="created\n",
        expected_postimage_sha256=_sha("created\n"),
    )
    proposal = service.propose(root, plan_id, task_id, MutationRequest(operations=(_replace(0), create)))
    service.decide(plan_id, proposal.proposal_id, "Human", MutationDecision.APPROVED)

    def fail(_path: str, count: int) -> None:
        if count == 2:
            raise RuntimeError("injected failure")

    service.after_target_applied = fail
    record = service.apply(root, plan_id, proposal.proposal_id)
    assert record.status == MutationStatus.APPLY_FAILED_ROLLED_BACK and record.rollback_complete, record.error_codes
    assert (root / "src/file0.py").read_text() == "old-0\n"
    assert not (root / "src/new.py").exists()


def test_maximum_target_proposal_is_stable_and_bounded(tmp_path: Path) -> None:
    root, plan_id, task_id = _ready_repo(tmp_path, files=16)
    service = _service(root)
    operations = tuple(_replace(index) for index in reversed(range(16)))
    proposal = service.propose(root, plan_id, task_id, MutationRequest(operations=operations))
    assert proposal.operation_count == 16 and len(proposal.targets) == 16
    assert proposal.total_write_bytes <= 8 * 1024 * 1024


def test_cli_validate_propose_approve_apply_and_records(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    root, plan_id, task_id = _ready_repo(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("G3 must not use subprocess or network")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    request_file = tmp_path / "mutation.json"
    request_file.write_text(
        json.dumps(MutationRequest(operations=(_replace(0),)).model_dump(mode="json")), encoding="utf-8"
    )
    runner = CliRunner()
    before = (root / "src/file0.py").read_bytes()
    human = runner.invoke(app, ["repo", "patch", "validate", "--file", str(request_file), "--repo", str(root)])
    structured = runner.invoke(
        app, ["repo", "patch", "validate", "--file", str(request_file), "--repo", str(root), "--json"]
    )
    assert human.exit_code == structured.exit_code == 0
    assert json.loads(structured.stdout)["valid"]
    assert (root / "src/file0.py").read_bytes() == before
    proposed = runner.invoke(
        app,
        [
            "plan",
            "mutation",
            "propose",
            plan_id,
            "--task",
            task_id,
            "--file",
            str(request_file),
            "--repo",
            str(root),
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert proposed.exit_code == 0, proposed.stdout
    proposal_id = json.loads(proposed.stdout)["proposal_id"]
    assert (root / "src/file0.py").read_bytes() == before
    shown = runner.invoke(
        app,
        ["plan", "mutation", "show", plan_id, proposal_id, "--project-root", str(root), "--json"],
    )
    assert shown.exit_code == 0 and json.loads(shown.stdout)["diff_preview"]
    approved = runner.invoke(
        app,
        [
            "plan",
            "mutation",
            "approve",
            plan_id,
            proposal_id,
            "--actor",
            "Human",
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert approved.exit_code == 0 and (root / "src/file0.py").read_bytes() == before
    applied = runner.invoke(
        app,
        [
            "plan",
            "mutation",
            "apply",
            plan_id,
            proposal_id,
            "--repo",
            str(root),
            "--project-root",
            str(root),
            "--json",
        ],
    )
    payload = json.loads(applied.stdout)
    assert applied.exit_code == 0 and payload["status"] == "applied"
    listed = runner.invoke(app, ["tool", "mutation", "list", "--project-root", str(root), "--json"])
    mutation_id = json.loads(listed.stdout)[0]["mutation_id"]
    shown_record = runner.invoke(app, ["tool", "mutation", "show", mutation_id, "--project-root", str(root), "--json"])
    assert shown_record.exit_code == 0 and "content" not in json.loads(shown_record.stdout)


def test_module_entrypoint_exposes_mutation_workflow() -> None:
    runner = CliRunner()
    for arguments, expected in (
        (["repo", "patch", "--help"], "validate"),
        (["plan", "mutation", "--help"], "propose"),
        (["tool", "mutation", "--help"], "show"),
    ):
        result = runner.invoke(app, arguments)
        assert result.exit_code == 0 and expected in result.stdout


def test_stale_preimage_is_rejected_before_any_apply(tmp_path: Path) -> None:
    root, plan_id, task_id = _ready_repo(tmp_path)
    service = _service(root)
    proposal = service.propose(root, plan_id, task_id, MutationRequest(operations=(_replace(0),)))
    service.decide(plan_id, proposal.proposal_id, "Human", MutationDecision.APPROVED)
    (root / "src/file0.py").write_bytes(b"external-change\n")
    with pytest.raises(MutationAdmissionError, match="snapshot"):
        service.apply(root, plan_id, proposal.proposal_id)
    assert (root / "src/file0.py").read_bytes() == b"external-change\n"
