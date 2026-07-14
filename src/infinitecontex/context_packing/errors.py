"""Typed context-manifest errors."""


class ContextPackingError(RuntimeError):
    pass


class ContextManifestNotFoundError(ContextPackingError):
    pass


class ContextManifestFormatError(ContextPackingError):
    pass
