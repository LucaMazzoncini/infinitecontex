"""Versioned deterministic G5 review policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel

from infinitecontex.planning.models import EvidenceType
from infinitecontex.tools.validation_models import CommandCategory, ExitClassification

MAX_EVIDENCE_PER_REVIEW = 1000
SUPPORTED_METHODS = {
    "test": (EvidenceType.TEST_RESULT, frozenset({CommandCategory.TEST})),
    "tests": (EvidenceType.TEST_RESULT, frozenset({CommandCategory.TEST})),
    "build": (EvidenceType.BUILD_RESULT, frozenset({CommandCategory.BUILD})),
    "format": (EvidenceType.STATIC_ANALYSIS_RESULT, frozenset({CommandCategory.FORMAT})),
    "lint": (EvidenceType.STATIC_ANALYSIS_RESULT, frozenset({CommandCategory.LINT})),
    "type_check": (EvidenceType.STATIC_ANALYSIS_RESULT, frozenset({CommandCategory.STATIC_ANALYSIS})),
    "static_analysis": (
        EvidenceType.STATIC_ANALYSIS_RESULT,
        frozenset({CommandCategory.FORMAT, CommandCategory.LINT, CommandCategory.STATIC_ANALYSIS}),
    ),
    "package_verification": (EvidenceType.BUILD_RESULT, frozenset({CommandCategory.BUILD})),
}


@dataclass(frozen=True)
class EvidenceReviewPolicy:
    version: int = 1
    supported_evidence_schema: int = 1
    supported_execution_schema: int = 1
    supported_command_version: str = "1.0.0"
    require_explicit_criterion_link: bool = True
    require_same_before_after_snapshot: bool = True
    accept_dirty_repository: bool = True
    accept_redacted_output_with_warning: bool = True
    accept_truncated_output: bool = False
    accepted_classifications: tuple[str, ...] = (ExitClassification.PASSED.value,)
    maximum_evidence: int = MAX_EVIDENCE_PER_REVIEW
    default_aggregation: str = "all_required"
    human_decision_required: bool = True

    @property
    def fingerprint(self) -> str:
        return fingerprint(
            {
                "version": self.version,
                "supported_evidence_schema": self.supported_evidence_schema,
                "supported_execution_schema": self.supported_execution_schema,
                "supported_command_version": self.supported_command_version,
                "require_explicit_criterion_link": self.require_explicit_criterion_link,
                "require_same_before_after_snapshot": self.require_same_before_after_snapshot,
                "accept_dirty_repository": self.accept_dirty_repository,
                "accept_redacted_output_with_warning": self.accept_redacted_output_with_warning,
                "accept_truncated_output": self.accept_truncated_output,
                "accepted_classifications": self.accepted_classifications,
                "maximum_evidence": self.maximum_evidence,
                "default_aggregation": self.default_aggregation,
                "human_decision_required": self.human_decision_required,
            }
        )


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def model_fingerprint(value: BaseModel, *excluded: str) -> str:
    payload = value.model_dump(mode="json", exclude=set(excluded))
    return fingerprint(payload)
