"""Typed fail-closed G3 mutation errors."""


class MutationError(RuntimeError):
    code = "mutation_error"


class MutationAdmissionError(MutationError):
    code = "mutation_admission_failed"


class MutationPersistenceError(MutationError):
    code = "mutation_persistence_failed"


class MutationApplyError(MutationError):
    code = "mutation_apply_failed"


class MutationRollbackError(MutationApplyError):
    code = "mutation_rollback_incomplete"
