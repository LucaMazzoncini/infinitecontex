"""Typed failures for deterministic task-context inspection."""


class TaskContextError(RuntimeError):
    """Base task-context failure."""


class RepositoryNotFoundError(TaskContextError):
    pass


class InvalidRepositoryRootError(TaskContextError):
    pass


class RepositoryEscapeError(TaskContextError):
    pass


class UnsupportedFilesystemPathError(TaskContextError):
    pass


class UnsafeSymlinkError(TaskContextError):
    pass


class PathResolutionError(TaskContextError):
    pass


class PathMissingError(PathResolutionError):
    pass


class PathAmbiguousError(PathResolutionError):
    pass


class GlobLimitExceededError(PathResolutionError):
    pass


class BinarySourceError(PathResolutionError):
    pass


class SourceTooLargeError(PathResolutionError):
    pass


class InvalidSourceRangeError(PathResolutionError):
    pass


class ContentHashMismatchError(PathResolutionError):
    pass


class StaleRepositorySnapshotError(PathResolutionError):
    pass


class SymbolResolutionError(TaskContextError):
    pass


class SymbolMissingError(SymbolResolutionError):
    pass


class SymbolAmbiguousError(SymbolResolutionError):
    pass


class UnsupportedSymbolLanguageError(SymbolResolutionError):
    pass


class SymbolSyntaxError(SymbolResolutionError):
    pass


class ScopeConflictError(TaskContextError):
    pass


class TaskContextProfileError(TaskContextError):
    pass


class MissingProfileError(TaskContextProfileError):
    pass


class WeakProfileIdentityError(TaskContextProfileError):
    pass


class ProfileDigestMismatchError(TaskContextProfileError):
    pass


class TaskContextPersistenceError(TaskContextError):
    pass


class TaskContextNotFoundError(TaskContextPersistenceError):
    pass


class StaleTaskContextAnalysisError(TaskContextError):
    pass
