"""Versioned fail-closed G6 transition policy."""

from __future__ import annotations

import hashlib

import orjson

from infinitecontex.evidence_review.models import ActorType, Confidence, ReviewOutcome
from infinitecontex.planning.models import CriterionStatus

POLICY_VERSION = 1
MAX_CRITERIA_TRANSITIONS = 1000
CRITERION_TRANSITIONS = {
    CriterionStatus.PENDING: frozenset(
        {CriterionStatus.SATISFIED, CriterionStatus.FAILED, CriterionStatus.WAIVED, CriterionStatus.NOT_APPLICABLE}
    ),
    CriterionStatus.FAILED: frozenset({CriterionStatus.PENDING, CriterionStatus.SATISFIED, CriterionStatus.WAIVED}),
    CriterionStatus.SATISFIED: frozenset({CriterionStatus.PENDING, CriterionStatus.SUPERSEDED}),
    CriterionStatus.WAIVED: frozenset({CriterionStatus.PENDING, CriterionStatus.SUPERSEDED}),
    CriterionStatus.NOT_APPLICABLE: frozenset({CriterionStatus.PENDING, CriterionStatus.SUPERSEDED}),
    CriterionStatus.SUPERSEDED: frozenset(),
}


class TransitionPolicy:
    version = POLICY_VERSION
    minimum_confidence = Confidence.MEDIUM
    accepted_support_outcomes = frozenset({ReviewOutcome.SUPPORTED, ReviewOutcome.SUPPORTED_WITH_WARNING})
    accepted_actor_types = frozenset({ActorType.HUMAN, ActorType.IMPORTED_EXTERNAL_REVIEWER})

    @property
    def fingerprint(self) -> str:
        payload = {
            "version": self.version,
            "minimum_confidence": self.minimum_confidence.value,
            "support": sorted(x.value for x in self.accepted_support_outcomes),
            "actors": sorted(x.value for x in self.accepted_actor_types),
            "criterion": {
                k.value: sorted(v.value for v in values)
                for k, values in sorted(CRITERION_TRANSITIONS.items(), key=lambda x: x[0].value)
            },
        }
        return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()

    def validate_criterion(self, current: CriterionStatus, target: CriterionStatus) -> None:
        if target not in CRITERION_TRANSITIONS[current]:
            raise ValueError(f"Criterion transition {current.value} -> {target.value} is forbidden")


def model_fingerprint(value: object, *exclude: str) -> str:
    from pydantic import BaseModel

    payload = value.model_dump(mode="json", exclude=set(exclude)) if isinstance(value, BaseModel) else value
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()
