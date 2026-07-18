"""Conservative versioned sensitive repository-path denial policy."""

from __future__ import annotations

import fnmatch
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SensitivePathPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: Literal["sensitive-repository-paths-v1"] = "sensitive-repository-paths-v1"
    version: Literal[1] = 1
    denied_names: tuple[str, ...] = (
        ".env",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "secrets.json",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    )
    denied_globs: tuple[str, ...] = (
        ".env.*",
        "*.key",
        "*.pem",
        "*.p12",
        "*.pfx",
        "*.jks",
        "*.keystore",
        "*private-key*",
        "*private_key*",
        "*credentials*",
        "*secrets*",
        "*token-store*",
    )
    denied_segments: tuple[str, ...] = (".ssh", ".aws", ".azure", ".gnupg")

    def reason(self, path: str) -> str | None:
        normalized = path.replace("\\", "/").strip("/")
        segments = tuple(part.casefold() for part in normalized.split("/") if part)
        if any(part in self.denied_segments for part in segments):
            return "sensitive_directory"
        name = segments[-1] if segments else ""
        if name in self.denied_names:
            return "sensitive_filename"
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in self.denied_globs):
            return "sensitive_filename_pattern"
        return None

    def permits(self, path: str) -> bool:
        return self.reason(path) is None
