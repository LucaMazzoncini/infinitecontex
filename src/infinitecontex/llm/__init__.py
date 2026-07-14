"""Local language-model provider abstractions."""

from infinitecontex.llm.base import LLMClient
from infinitecontex.llm.ollama import OllamaClient

__all__ = ["LLMClient", "OllamaClient"]
