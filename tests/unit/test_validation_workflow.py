from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from infinitecontex.cli import app
from infinitecontex.llm.models import InstalledModel, ModelDetails
from infinitecontex.model_profiles.service import ModelProfileService
from infinitecontex.model_profiles.store import ModelProfileStore
from infinitecontex.storage.layout import build_layout
from infinitecontex.tools.validation_models import StreamResult, ValidationDecision
from infinitecontex.tools.validation_runner import ProcessOutcome
from infinitecontex.validation_cli import _service


class IdentityClient:
    def list_models(self) -> list[InstalledModel]:
        return [InstalledModel(name="demo:latest", digest="sha256:test")]

    def show_model(self, name: str) -> ModelDetails:
        return ModelDetails(name=name, parameters="num_ctx 8192", model_info={"demo.context_length": 8192})


class FakeRunner:
    def __init__(self, *, mutate: bool = False) -> None:
        self.calls = 0
        self.mutate = mutate

    def run(self, executable: str, arguments: tuple[str, ...], **kwargs: object) -> ProcessOutcome:
        self.calls += 1
        if self.mutate:
            Path(kwargs["cwd"]).joinpath("tests/test_validation.py").write_text("changed\n", encoding="utf-8")  # type: ignore[arg-type]
        empty = StreamResult(
            byte_count=0,
            line_count=0,
            sha256=hashlib.sha256(b"").hexdigest(),
            preview="",
            truncated=False,
            redacted=False,
        )
        return ProcessOutcome(
            exit_code=0, timed_out=False, cancelled=False, output_limit_exceeded=False, stdout=empty, stderr=empty
        )


def _ready(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "tests/test_validation.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    layout = build_layout(root)
    ModelProfileService(
        IdentityClient(),
        ModelProfileStore(layout.model_profiles),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 7, 18, tzinfo=UTC),
    ).create_or_reuse("demo:latest")
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(
        json.dumps(
            {
                "stable_plan_key": "g4-validation",
                "title": "G4 validation",
                "objective": "Run bounded tests.",
                "status": "active",
                "planner_provenance": "human_authored",
                "repository_ref": "test",
                "tasks": [
                    {
                        "task_key": "validate",
                        "title": "Validate",
                        "objective": "Run exact tests.",
                        "task_type": "validation",
                        "status": "ready",
                        "affected_scopes": ["tests/**"],
                        "forbidden_scopes": ["tests/forbidden/**"],
                        "requested_capabilities": ["run_tests"],
                        "context_requirements": {"required_files": ["tests/test_validation.py"]},
                        "provenance": "human_authored",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    imported = runner.invoke(app, ["plan", "import", "--file", str(plan_file), "--project-root", str(root), "--json"])
    assert imported.exit_code == 0, imported.stdout
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


def test_proposal_and_approval_launch_nothing_then_exact_run_and_evidence(tmp_path: Path) -> None:
    root, plan_id, task_id = _ready(tmp_path)
    service = _service(root)
    fake = FakeRunner()
    service.runner = fake  # type: ignore[assignment]
    before = (root / "tests/test_validation.py").read_bytes()
    proposal = service.propose(root, plan_id, task_id, "python.pytest", {"target": ("tests/test_validation.py",)})
    repeated = service.propose(root, plan_id, task_id, "python.pytest", {"target": ("tests/test_validation.py",)})
    assert proposal.semantic_fingerprint == repeated.semantic_fingerprint
    assert fake.calls == 0 and (root / "tests/test_validation.py").read_bytes() == before
    with pytest.raises(ValueError, match="approval"):
        service.run(root, plan_id, proposal.proposal_id)
    service.decide(plan_id, proposal.proposal_id, "Human", ValidationDecision.APPROVED)
    assert fake.calls == 0 and (root / "tests/test_validation.py").read_bytes() == before
    record = service.run(root, plan_id, proposal.proposal_id, criterion_id="criterion-1")
    assert fake.calls == 1 and record.classification.value == "passed"
    assert service.store.list_evidence()[0].criterion_id == "criterion-1"
    assert (root / "tests/test_validation.py").read_bytes() == before


def test_rejection_scope_capability_staleness_and_mutation_fail_closed(tmp_path: Path) -> None:
    root, plan_id, task_id = _ready(tmp_path)
    service = _service(root)
    with pytest.raises(ValueError):
        service.propose(root, plan_id, task_id, "python.pytest", {"target": ("../escape.py",)})
    proposal = service.propose(root, plan_id, task_id, "python.pytest", {"target": ("tests/test_validation.py",)})
    service.decide(plan_id, proposal.proposal_id, "Human", ValidationDecision.REJECTED)
    with pytest.raises(ValueError, match="approval"):
        service.run(root, plan_id, proposal.proposal_id)

    root2, plan2, task2 = _ready(tmp_path / "other")
    contaminating = _service(root2)
    contaminating.runner = FakeRunner(mutate=True)  # type: ignore[assignment]
    proposed = contaminating.propose(root2, plan2, task2, "python.pytest", {"target": ("tests/test_validation.py",)})
    contaminating.decide(plan2, proposed.proposal_id, "Human", ValidationDecision.APPROVED)
    record = contaminating.run(root2, plan2, proposed.proposal_id)
    assert record.classification.value == "repository_mutation_detected"
    assert record.unexpected_changed_paths == ("tests/test_validation.py",)
    assert contaminating.store.list_evidence() == ()
