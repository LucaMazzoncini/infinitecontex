"""Typed fail-closed errors for G5 evidence review."""


class EvidenceReviewError(ValueError):
    code = "evidence_review_error"


class ReviewPlanError(EvidenceReviewError):
    code = "plan_missing_or_revision_mismatch"


class ReviewTaskError(EvidenceReviewError):
    code = "task_missing"


class ReviewCriterionError(EvidenceReviewError):
    code = "criterion_missing_or_unsupported"


class ReviewEvidenceError(EvidenceReviewError):
    code = "evidence_missing_or_invalid"


class ReviewExecutionError(EvidenceReviewError):
    code = "execution_missing_or_invalid"


class ReviewFreshnessError(EvidenceReviewError):
    code = "stale_evidence"


class ReviewContaminationError(EvidenceReviewError):
    code = "contaminated_evidence"


class ReviewPersistenceError(EvidenceReviewError):
    code = "evidence_review_persistence_failed"


class ReviewDecisionError(EvidenceReviewError):
    code = "evidence_review_decision_invalid"
