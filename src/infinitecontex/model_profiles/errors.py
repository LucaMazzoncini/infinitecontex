"""Typed model-profile persistence failures."""


class ModelProfileError(RuntimeError):
    """Base model-profile failure."""


class ModelProfileNotFoundError(ModelProfileError):
    """No exact profile exists for an identity."""


class ModelProfileDigestMismatchError(ModelProfileError):
    """Profiles exist for the model name, but not the current digest."""


class ModelProfileFormatError(ModelProfileError):
    """A persisted profile is malformed or unsupported."""
