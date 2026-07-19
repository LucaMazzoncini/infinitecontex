"""Provider-neutral language-model contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OllamaSampling(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, gt=0.0, le=1.0)
    top_k: int = Field(default=40, ge=0, le=1000)
    seed: int = 7
    repeat_penalty: float = Field(default=1.1, gt=0.0, le=2.0)
    stop: tuple[str, ...] = ()
    num_predict: int = Field(gt=0)
    num_ctx: int = Field(gt=0)

    def transmitted(self) -> dict[str, object]:
        return self.model_dump(mode="json")


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
