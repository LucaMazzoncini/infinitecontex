"""Versioned fail-closed repository-resolution policy."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from infinitecontex.context_admission.policy import AdmissionPolicy


class RepositoryResolutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: Literal["deterministic-repository-resolution-v1"] = "deterministic-repository-resolution-v1"
    version: Literal[1] = 1
    include_tracked_files: Literal[True] = True
    include_permitted_untracked_files: bool = True
    respect_git_ignore: Literal[True] = True
    ignore_policy_version: Literal[1] = 1
    symlink_policy: Literal["reject"] = "reject"
    path_case_policy: Literal["sensitive", "insensitive"] = Field(
        default_factory=lambda: "insensitive" if os.name == "nt" else "sensitive"
    )
    accepted_encodings: tuple[Literal["utf-8"], ...] = ("utf-8",)
    maximum_inventory_files: int = Field(default=100_000, ge=1, le=1_000_000)
    maximum_source_file_bytes: int = Field(default=1024 * 1024, ge=1)
    maximum_total_source_bytes: int = Field(default=32 * 1024 * 1024, ge=1)
    maximum_path_references: int = Field(default=256, ge=1, le=10_000)
    maximum_symbol_references: int = Field(default=256, ge=1, le=10_000)
    maximum_glob_matches: int = Field(default=256, ge=1, le=10_000)
    maximum_symbol_matches: int = Field(default=128, ge=1, le=10_000)
    permit_dirty_repository: bool = True
    permit_stale_analysis_display: bool = True
    fail_closed: Literal[True] = True
    python_resolver_id: Literal["python-ast-symbol-resolver-v1"] = "python-ast-symbol-resolver-v1"
    python_resolver_version: Literal[1] = 1
    excluded_prefixes: tuple[str, ...] = (
        ".git/",
        ".infctx/",
        ".venv/",
        "venv/",
        "node_modules/",
        "__pycache__/",
        ".pytest_cache/",
        ".mypy_cache/",
        ".ruff_cache/",
        "build/",
        "dist/",
        "Library/",
        "Temp/",
        "Logs/",
        "Obj/",
        "Build/",
        "Builds/",
    )
    excluded_suffixes: tuple[str, ...] = (".pyc", ".pyo")

    def admission_policy(self) -> AdmissionPolicy:
        return AdmissionPolicy()
