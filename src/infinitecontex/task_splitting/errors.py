"""Typed failures for deterministic task splitting and approval."""


class TaskSplitError(RuntimeError):
    code = "task_split_error"


class SplitEligibilityError(TaskSplitError):
    code = "task_not_eligible"


class SplitContractError(TaskSplitError):
    code = "contract_coverage_incomplete"


class SplitBoundsError(TaskSplitError):
    code = "split_bounds_exceeded"


class SplitProposalError(TaskSplitError):
    code = "proposal_invalid"


class SplitProposalStaleError(SplitProposalError):
    code = "proposal_stale"


class SplitApprovalError(TaskSplitError):
    code = "approval_invalid"


class SplitPersistenceError(TaskSplitError):
    code = "split_persistence_failure"


class SplitNotFoundError(SplitPersistenceError):
    code = "split_record_not_found"
