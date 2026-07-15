"""Typed failures for strict plan validation and persistence."""


class PlanningError(RuntimeError):
    """Base planning-foundation failure."""


class PlanValidationError(PlanningError):
    pass


class PlanCycleError(PlanValidationError):
    pass


class PlanTransitionError(PlanValidationError):
    pass


class PlanPersistenceError(PlanningError):
    pass


class PlanNotFoundError(PlanPersistenceError):
    pass


class PlanRevisionNotFoundError(PlanPersistenceError):
    pass


class PlanFormatError(PlanPersistenceError):
    pass
