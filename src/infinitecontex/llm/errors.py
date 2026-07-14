"""Typed failures exposed by local model providers."""


class LLMError(RuntimeError):
    """Base error for model-provider failures."""


class LLMConnectionError(LLMError):
    """The configured provider could not be reached."""


class LLMTimeoutError(LLMError):
    """The provider did not respond within the configured timeout."""


class LLMResponseError(LLMError):
    """The provider returned an invalid or unsuccessful response."""


class LLMModelError(LLMError):
    """The requested model is unavailable or invalid."""
