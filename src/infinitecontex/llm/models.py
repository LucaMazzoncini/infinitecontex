"""Provider-neutral language-model contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ProviderHealth(BaseModel):
    available: bool
    version: str


class InstalledModel(BaseModel):
    name: str
    model: str = ""
    digest: str = ""
    size: int = 0
    modified_at: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ModelDetails(BaseModel):
    name: str
    digest: str = ""
    format: str = ""
    parameters: str = ""
    capabilities: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    model_info: dict[str, Any] = Field(default_factory=dict)


class ChatChunk(BaseModel):
    content: str = ""
    done: bool = False
    prompt_eval_count: int | None = None
    eval_count: int | None = None
