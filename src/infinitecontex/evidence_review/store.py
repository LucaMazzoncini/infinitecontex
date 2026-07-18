"""Create-only atomic persistence for G5 reviews and human decisions."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import TypeVar

import orjson
from pydantic import BaseModel, ValidationError

from infinitecontex.evidence_review.errors import ReviewPersistenceError
from infinitecontex.evidence_review.models import EvidenceReview, EvidenceReviewDecision
from infinitecontex.evidence_review.policy import model_fingerprint

_T = TypeVar("_T", bound=BaseModel)
_REVIEW = re.compile(r"^evidence-review-[0-9a-f]{24}$")
_DECISION = re.compile(r"^evidence-review-decision-[0-9a-f]{24}$")


class EvidenceReviewStore:
    def __init__(self, plans_directory: Path) -> None:
        self.plans_directory = plans_directory

    def save_review(self, value: EvidenceReview) -> Path:
        self._verify(value)
        target = self._review_directory(value.plan_id, value.plan_revision, value.task_id) / f"{value.review_id}.json"
        if target.exists():
            existing = self.load_review(value.plan_id, value.review_id)
            if existing.semantic_fingerprint == value.semantic_fingerprint:
                return target
        return self._save(target, value)

    def save_decision(self, value: EvidenceReviewDecision) -> Path:
        self._verify(value)
        existing = self.list_decisions(value.plan_id, review_id=value.review_id)
        if existing:
            if existing[0].decision_fingerprint == value.decision_fingerprint:
                return self._decision_directory(value.plan_id) / f"{value.decision_id}.json"
            raise ReviewPersistenceError("This evidence review already has an immutable human decision")
        return self._save(self._decision_directory(value.plan_id) / f"{value.decision_id}.json", value)

    def load_review(self, plan_id: str, review_id: str) -> EvidenceReview:
        if not _REVIEW.fullmatch(review_id):
            raise ReviewPersistenceError("Invalid evidence review ID")
        root = self.plans_directory / plan_id / "evidence-reviews"
        matches = tuple(root.glob(f"*/*/{review_id}.json")) if root.exists() else ()
        if len(matches) != 1:
            raise ReviewPersistenceError(f"Evidence review {review_id} was not found")
        value = self._load(matches[0], EvidenceReview)
        self._verify(value)
        return value

    def load_decision(self, plan_id: str, decision_id: str) -> EvidenceReviewDecision:
        if not _DECISION.fullmatch(decision_id):
            raise ReviewPersistenceError("Invalid evidence review decision ID")
        value = self._load(self._decision_directory(plan_id) / f"{decision_id}.json", EvidenceReviewDecision)
        self._verify(value)
        return value

    def list_reviews(self, plan_id: str, *, task_id: str | None = None) -> tuple[EvidenceReview, ...]:
        root = self.plans_directory / plan_id / "evidence-reviews"
        if not root.exists():
            return ()
        values = tuple(self._load(path, EvidenceReview) for path in sorted(root.glob("*/*/*.json")))
        for value in values:
            self._verify(value)
        return tuple(value for value in values if task_id is None or value.task_id == task_id)

    def list_decisions(self, plan_id: str, *, review_id: str | None = None) -> tuple[EvidenceReviewDecision, ...]:
        root = self._decision_directory(plan_id)
        if not root.exists():
            return ()
        values = tuple(self._load(path, EvidenceReviewDecision) for path in sorted(root.glob("*.json")))
        for value in values:
            self._verify(value)
        return tuple(value for value in values if review_id is None or value.review_id == review_id)

    def _review_directory(self, plan_id: str, revision: int, task_id: str) -> Path:
        return self.plans_directory / plan_id / "evidence-reviews" / str(revision) / task_id

    def _decision_directory(self, plan_id: str) -> Path:
        return self.plans_directory / plan_id / "evidence-review-decisions"

    @staticmethod
    def serialize(value: BaseModel) -> bytes:
        return orjson.dumps(
            value.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE
        )

    @classmethod
    def _save(cls, target: Path, value: BaseModel) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ReviewPersistenceError(f"Immutable review record {target.stem} already exists")
        descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(cls.serialize(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, target)
        except OSError as exc:
            raise ReviewPersistenceError(f"Could not persist immutable review record: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return target

    @staticmethod
    def _load(path: Path, model: type[_T]) -> _T:
        if not path.exists():
            raise ReviewPersistenceError(f"Review record {path.stem} was not found")
        try:
            return model.model_validate(orjson.loads(path.read_bytes()))
        except (OSError, ValueError, ValidationError, orjson.JSONDecodeError) as exc:
            raise ReviewPersistenceError(f"Review record {path.name} is malformed or unsupported") from exc

    @staticmethod
    def _verify(value: BaseModel) -> None:
        if isinstance(value, EvidenceReview):
            fingerprint = model_fingerprint(value, "semantic_fingerprint", "review_id", "created_at")
            actual, value_id, prefix = value.semantic_fingerprint, value.review_id, "evidence-review-"
        elif isinstance(value, EvidenceReviewDecision):
            fingerprint = model_fingerprint(value, "decision_fingerprint", "decision_id", "decided_at")
            actual, value_id, prefix = value.decision_fingerprint, value.decision_id, "evidence-review-decision-"
        else:
            raise ReviewPersistenceError("Unsupported evidence review record")
        if fingerprint != actual or value_id != prefix + fingerprint[:24]:
            raise ReviewPersistenceError("Evidence review record fingerprint is invalid")
