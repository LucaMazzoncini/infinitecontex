"""Configuration loading and merge order.

Priority (high -> low): env vars, repo config, global config, defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import orjson
from pydantic import BaseModel, ConfigDict, Field

from infinitecontex.core.policies import RuntimePolicies


class LLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "auto"
    fallback_models: list[str] = Field(default_factory=lambda: ["qwen3-coder:30b"])
    keep_alive: str = "10m"
    request_timeout_seconds: float = 900.0


class ChatConfig(BaseModel):
    auto_snapshot: bool = True
    recent_turns: int = Field(default=6, ge=1, le=50)
    stream: bool = True


class ContextBudgetConfig(BaseModel):
    warning_threshold_basis_points: int = Field(default=8750, ge=1, le=10000)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    project_name: str = ""
    capture_max_files: int = 1500
    include_patterns: list[str] = Field(default_factory=lambda: ["**/*.py", "**/*.md", "pyproject.toml"])
    exclude_patterns: list[str] = Field(
        default_factory=lambda: [
            ".git/**",
            ".infctx/**",
            ".venv/**",
            "node_modules/**",
            ".pytest_cache/**",
            ".mypy_cache/**",
            ".ruff_cache/**",
            "**/*.pyc",
            "build/**",
            "dist/**",
            ".github/skills/**",
        ]
    )
    modes: list[str] = Field(
        default_factory=lambda: [
            "copilot-restore",
            "claude-code-restore",
            "generic-agent-restore",
            "human-handoff",
        ]
    )
    policies: RuntimePolicies = Field(default_factory=RuntimePolicies)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)
    context_budget: ContextBudgetConfig = Field(default_factory=ContextBudgetConfig)


@dataclass(frozen=True)
class ConfigPaths:
    global_path: Path
    repo_path: Path


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return cast(dict[str, Any], orjson.loads(path.read_bytes()))


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def get_config_paths(project_root: Path) -> ConfigPaths:
    home = Path.home()
    global_dir = home / ".config" / "infinitecontex"
    return ConfigPaths(global_path=global_dir / "config.json", repo_path=project_root / ".infctx" / "config.json")


def load_app_config(project_root: Path) -> AppConfig:
    paths = get_config_paths(project_root)
    defaults = AppConfig().model_dump()
    global_cfg = _read_json(paths.global_path)
    repo_cfg = _read_json(paths.repo_path)

    merged = _deep_merge(defaults, global_cfg)
    merged = _deep_merge(merged, repo_cfg)

    env_budget = _env_int("INFCTX_TOKEN_BUDGET")
    if env_budget is not None:
        merged.setdefault("policies", {}).setdefault("token", {})["default_budget"] = env_budget

    return AppConfig.model_validate(merged)


def _env_int(key: str) -> int | None:
    import os

    value = os.getenv(key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def save_repo_config(project_root: Path, config: AppConfig) -> None:
    cfg_path = project_root / ".infctx" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_bytes(orjson.dumps(config.model_dump(), option=orjson.OPT_INDENT_2))
