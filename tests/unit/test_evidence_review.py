from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from infinitecontex.cli import app
from infinitecontex.evidence_review.models import ActorType, Confidence, ReviewDecisionValue, ReviewOutcome
from infinitecontex.evidence_review.policy import EvidenceReviewPolicy
from infinitecontex.evidence_review.service import EvidenceReviewService
from infinitecontex.evidence_review.store import EvidenceReviewStore
from infinitecontex.llm.models import InstalledModel, ModelDetails
from infinitecontex.model_profiles.service import ModelProfileService
from infinitecontex.model_profiles.store import ModelProfileStore
from infinitecontex.storage.layout import build_layout
from infinitecontex.tools.validation_definitions import evidence_fingerprint, record_fingerprint
from infinitecontex.tools.validation_models import (
    ExitClassification,
    StreamResult,
    ValidationDecision,
)
from infinitecontex.tools.validation_runner import ProcessOutcome
from infinitecontex.validation_cli import _service as validation_service


class IdentityClient:
    def list_models(self) -> list[InstalledModel]:
        return [InstalledModel(name="demo:latest", digest="sha256:test")]

    def show_model(self, name: str) -> ModelDetails:
        return ModelDetails(name=name, parameters="num_ctx 8192", model_info={"demo.context_length": 8192})


class FakeRunner:
    def run(self, *_args: object, **_kwargs: object) -> ProcessOutcome:
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


def _ready(tmp_path: Path, *, minimum: int = 1, task_status: str = "ready") -> tuple[Path, str, str, str]:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "tests/test_review.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    layout = build_layout(root)
    ModelProfileService(
        IdentityClient(),
        ModelProfileStore(layout.model_profiles),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 7, 19, tzinfo=UTC),
    ).create_or_reuse("demo:latest")
    criterion_id = "criterion-tests"
    requirements = [
        {
            "evidence_id": f"required-{index}",
            "evidence_type": "test_result",
            "description": f"test evidence {index}",
            "mandatory": True,
        }
        for index in range(minimum)
    ]
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(
        json.dumps(
            {
                "stable_plan_key": "g5-review",
                "title": "G5 review",
                "objective": "Review evidence.",
                "status": "active",
                "planner_provenance": "human_authored",
                "repository_ref": "test",
                "tasks": [
                    {
                        "task_key": "review",
                        "title": "Review",
                        "objective": "Validate exact test evidence.",
                        "task_type": "validation",
                        "status": task_status,
                        "affected_scopes": ["tests/**"],
                        "forbidden_scopes": [],
                        "requested_capabilities": ["run_tests"],
                        "acceptance_criteria": [
                            {
                                "criterion_id": criterion_id,
                                "description": "Tests pass.",
                                "verification_method": "test",
                                "required_evidence_type": "test_result",
                                "mandatory": True,
                                "status": "pending",
                                "provenance": "human_authored",
                            }
                        ],
                        "required_evidence": requirements,
                        "context_requirements": {"required_files": ["tests/test_review.py"]},
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
    validation = validation_service(root)
    validation.runner = FakeRunner()  # type: ignore[assignment]
    proposal = validation.propose(root, plan_id, task_id, "python.pytest", {"target": ("tests/test_review.py",)})
    validation.decide(plan_id, proposal.proposal_id, "Human", decision=ValidationDecision.APPROVED)
    record = validation.run(root, plan_id, proposal.proposal_id, criterion_id=criterion_id)
    evidence = validation.store.list_evidence()[0]
    assert record.classification == ExitClassification.PASSED
    return root, plan_id, task_id, evidence.evidence_id


def _review_service(root: Path) -> EvidenceReviewService:
    layout = build_layout(root)
    validation = validation_service(root)
    return EvidenceReviewService(
        validation.plan_store,
        validation.store,
        EvidenceReviewStore(layout.plans),
        analysis_store=validation.analysis_store,
    )


def _clone_evidence(
    root: Path, evidence_id: str, classification: ExitClassification, *, contaminated: bool = False, offset: int = 1
) -> str:
    service = validation_service(root)
    evidence = service.store.load_evidence(evidence_id)
    original = service.store.load_record(evidence.execution_id)
    provisional_record = original.model_copy(
        update={
            "execution_id": "tool-validation-" + "0" * 24,
            "record_fingerprint": "0" * 64,
            "classification": classification,
            "exit_code": 1 if classification == ExitClassification.VALIDATION_FAILED else 0,
            "unexpected_changed_paths": ("tests/test_review.py",) if contaminated else (),
            "completed_at": original.completed_at + timedelta(seconds=offset),
            "error_codes": (classification.value,) if classification != ExitClassification.PASSED else (),
        }
    )
    fp = record_fingerprint(provisional_record)
    record = provisional_record.model_copy(
        update={"record_fingerprint": fp, "execution_id": f"tool-validation-{fp[:24]}"}
    )
    service.store.save_record(record)
    provisional_evidence = evidence.model_copy(
        update={
            "evidence_id": "validation-evidence-" + "0" * 24,
            "fingerprint": "0" * 64,
            "execution_id": record.execution_id,
            "classification": classification,
            "created_at": evidence.created_at + timedelta(seconds=offset),
        }
    )
    evidence_fp = evidence_fingerprint(provisional_evidence)
    cloned = provisional_evidence.model_copy(
        update={"fingerprint": evidence_fp, "evidence_id": f"validation-evidence-{evidence_fp[:24]}"}
    )
    service.store.save_evidence(cloned)
    return cloned.evidence_id


def test_supported_review_and_human_decisions_never_change_plan(tmp_path: Path) -> None:
    root, plan_id, task_id, evidence_id = _ready(tmp_path)
    service = _review_service(root)
    before = service.plan_store.load_current(plan_id)
    review = service.review(root, plan_id, task_id, "criterion-tests", evidence_ids=(evidence_id,))
    assert review.outcome == ReviewOutcome.SUPPORTED
    assert review.evidence_supports_criterion and review.confidence.value == "high"
    assert review.criterion_status_unchanged and review.task_status_unchanged
    repeated = service.review(root, plan_id, task_id, "criterion-tests", evidence_ids=(evidence_id,))
    assert repeated.semantic_fingerprint == review.semantic_fingerprint
    accepted = service.decide(
        plan_id,
        review.review_id,
        "User",
        ReviewDecisionValue.ACCEPTED,
        reason="Evidence checked",
        actor_type=ActorType.HUMAN,
    )
    assert accepted.completes_criterion_or_task is False
    assert service.plan_store.load_current(plan_id) == before
    with pytest.raises(ValueError):
        service.decide(plan_id, review.review_id, "Other", ReviewDecisionValue.REJECTED, reason="No")
    with pytest.raises(ValueError):
        service.decide(plan_id, review.review_id, "LLM", ReviewDecisionValue.ACCEPTED, reason="No")


def test_missing_stale_failing_contaminated_duplicate_and_conflict_outcomes(tmp_path: Path) -> None:
    root, plan_id, task_id, evidence_id = _ready(tmp_path)
    service = _review_service(root)
    missing = service.review(root, plan_id, task_id, "criterion-tests", evidence_ids=())
    assert missing.outcome == ReviewOutcome.MISSING_EVIDENCE
    failing_id = _clone_evidence(root, evidence_id, ExitClassification.VALIDATION_FAILED)
    failed = service.review(root, plan_id, task_id, "criterion-tests", evidence_ids=(failing_id,))
    assert failed.outcome == ReviewOutcome.FAILED and not failed.evidence_supports_criterion
    contaminated_id = _clone_evidence(
        root, evidence_id, ExitClassification.REPOSITORY_MUTATION_DETECTED, contaminated=True, offset=2
    )
    contaminated = service.review(root, plan_id, task_id, "criterion-tests", evidence_ids=(contaminated_id,))
    assert contaminated.outcome == ReviewOutcome.CONTAMINATED_EVIDENCE
    duplicate_id = _clone_evidence(root, evidence_id, ExitClassification.PASSED, offset=3)
    duplicate = service.review(root, plan_id, task_id, "criterion-tests", evidence_ids=(evidence_id, duplicate_id))
    assert len(duplicate.duplicate_evidence_ids) == 1
    accounted = duplicate.accepted_evidence_ids + duplicate.duplicate_evidence_ids
    assert set(accounted) == {evidence_id, duplicate_id}
    conflict = service.review(root, plan_id, task_id, "criterion-tests", evidence_ids=(evidence_id, failing_id))
    assert conflict.outcome == ReviewOutcome.CONFLICTING_EVIDENCE
    (root / "tests/test_review.py").write_text("changed\n", encoding="utf-8")
    stale = service.review(root, plan_id, task_id, "criterion-tests", evidence_ids=(evidence_id,))
    assert stale.outcome == ReviewOutcome.STALE_EVIDENCE


def test_policy_fingerprint_store_compactness_and_cli_human_json(tmp_path: Path) -> None:
    assert EvidenceReviewPolicy().fingerprint == EvidenceReviewPolicy().fingerprint
    root, plan_id, task_id, evidence_id = _ready(tmp_path)
    service = _review_service(root)
    review = service.review(root, plan_id, task_id, "criterion-tests", evidence_ids=(evidence_id,))
    serialized = service.store.serialize(review).decode()
    assert "def test_ok" not in serialized and "PATH=" not in serialized
    runner = CliRunner()
    human = runner.invoke(
        app,
        [
            "plan",
            "evidence",
            "review",
            plan_id,
            "--task",
            task_id,
            "--criterion",
            "criterion-tests",
            "--evidence",
            evidence_id,
            "--repo",
            str(root),
            "--project-root",
            str(root),
        ],
    )
    assert human.exit_code == 0, human.stdout
    assert "Criterion status changed: NO" in human.stdout and "Task status changed: NO" in human.stdout
    shown = runner.invoke(
        app, ["plan", "evidence-review", "show", plan_id, review.review_id, "--project-root", str(root), "--json"]
    )
    assert shown.exit_code == 0 and json.loads(shown.stdout)["outcome"] == "supported"


def test_thousand_evidence_aggregation_is_bounded_and_order_independent(tmp_path: Path) -> None:
    root, _, _, evidence_id = _ready(tmp_path)
    evidence = validation_service(root).store.load_evidence(evidence_id)
    selected = tuple(evidence.model_copy(update={"evidence_id": f"evidence-{index:04d}"}) for index in range(1000))
    accepted = [item.evidence_id for item in selected]
    forward = EvidenceReviewService._outcome(
        selected,
        False,
        False,
        False,
        False,
        False,
        [],
        accepted,
        {ExitClassification.PASSED},
        1000,
        False,
    )
    reverse = EvidenceReviewService._outcome(
        tuple(reversed(selected)),
        False,
        False,
        False,
        False,
        False,
        [],
        list(reversed(accepted)),
        {ExitClassification.PASSED},
        1000,
        False,
    )
    assert forward == reverse == (ReviewOutcome.SUPPORTED, True, Confidence.HIGH)
