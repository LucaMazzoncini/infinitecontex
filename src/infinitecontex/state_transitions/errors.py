"""Typed G6 fail-closed transition errors."""


class StateTransitionError(ValueError):
    code = "state_transition_error"


class TransitionPlanError(StateTransitionError):
    code = "plan_or_revision_stale"


class TransitionTaskError(StateTransitionError):
    code = "task_missing_or_stale"


class TransitionCriterionError(StateTransitionError):
    code = "criterion_transition_invalid"


class TransitionReviewError(StateTransitionError):
    code = "review_missing_not_accepted_or_stale"


class TransitionProposalError(StateTransitionError):
    code = "proposal_invalid_or_stale"


class TransitionApprovalError(StateTransitionError):
    code = "approval_missing_rejected_or_invalid"


class TransitionDependencyError(StateTransitionError):
    code = "blocking_dependency"


class TransitionPersistenceError(StateTransitionError):
    code = "state_transition_persistence_failed"
