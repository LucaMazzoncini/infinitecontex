"""Deterministic data-only tool registry."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

import orjson

from infinitecontex.planning.models import Capability
from infinitecontex.tools.errors import (
    ConflictingToolFingerprintError,
    DuplicateToolIdentityError,
    ToolMissingError,
    UnsupportedRegistrySchemaError,
)
from infinitecontex.tools.fingerprints import sha256_payload
from infinitecontex.tools.models import (
    TOOL_REGISTRY_SCHEMA_VERSION,
    ImplementationStatus,
    ToolCategory,
    ToolDefinition,
    ToolRegistryExport,
)
from infinitecontex.tools.validation import validate_tool_definition


class ToolDefinitionProvider(Protocol):
    """Future data adapter; instances return definitions and are never dynamically loaded."""

    def definitions(self) -> Iterable[ToolDefinition]: ...


class ToolRegistry:
    def __init__(self, definitions: Iterable[ToolDefinition] = (), *, schema_version: int = 1) -> None:
        if schema_version != TOOL_REGISTRY_SCHEMA_VERSION:
            raise UnsupportedRegistrySchemaError(
                f"Unsupported tool registry schema {schema_version}; upgrade Infinite Context"
            )
        self.schema_version = schema_version
        self._by_id: dict[str, ToolDefinition] = {}
        self._by_name_version: dict[tuple[str, str], ToolDefinition] = {}
        for definition in definitions:
            self.register(definition)
        self.validate_all()

    def register(self, definition: ToolDefinition) -> None:
        validate_tool_definition(definition)
        existing_id = self._by_id.get(definition.tool_id)
        if existing_id is not None:
            if existing_id.definition_fingerprint != definition.definition_fingerprint:
                raise ConflictingToolFingerprintError(
                    f"Tool ID {definition.tool_id} has conflicting definition fingerprints"
                )
            raise DuplicateToolIdentityError(f"Tool ID {definition.tool_id} is already registered")
        key = (definition.canonical_name.casefold(), definition.tool_version)
        existing_name = self._by_name_version.get(key)
        if existing_name is not None:
            if existing_name.definition_fingerprint != definition.definition_fingerprint:
                raise ConflictingToolFingerprintError(
                    f"Tool {definition.canonical_name}@{definition.tool_version} conflicts with its registration"
                )
            raise DuplicateToolIdentityError(
                f"Tool {definition.canonical_name}@{definition.tool_version} is already registered"
            )
        self._by_id[definition.tool_id] = definition
        self._by_name_version[key] = definition

    def get(self, tool_id: str) -> ToolDefinition:
        try:
            return self._by_id[tool_id]
        except KeyError as exc:
            raise ToolMissingError(f"Tool {tool_id} was not found; run `infctx tool list`") from exc

    def get_by_name_version(self, canonical_name: str, tool_version: str) -> ToolDefinition:
        try:
            return self._by_name_version[(canonical_name.casefold(), tool_version)]
        except KeyError as exc:
            raise ToolMissingError(
                f"Tool {canonical_name}@{tool_version} was not found; run `infctx tool list`"
            ) from exc

    def list(
        self,
        *,
        category: ToolCategory | None = None,
        capability: Capability | None = None,
        implementation_status: ImplementationStatus | None = None,
    ) -> tuple[ToolDefinition, ...]:
        values: Iterable[ToolDefinition] = self._by_id.values()
        if category is not None:
            values = (item for item in values if item.category == category)
        if capability is not None:
            values = (item for item in values if capability in item.required_capabilities)
        if implementation_status is not None:
            values = (item for item in values if item.implementation_status == implementation_status)
        return tuple(sorted(values, key=lambda item: (item.canonical_name, item.tool_version, item.tool_id)))

    @property
    def fingerprint(self) -> str:
        return sha256_payload(
            {
                "schema_version": self.schema_version,
                "definitions": [item.definition_fingerprint for item in self.list()],
            }
        )

    def export(self) -> ToolRegistryExport:
        tools = self.list()
        return ToolRegistryExport(registry_fingerprint=self.fingerprint, tool_count=len(tools), tools=tools)

    def export_json(self) -> bytes:
        return orjson.dumps(
            self.export().model_dump(mode="json"),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE,
        )

    def validate_all(self) -> None:
        for definition in self.list():
            validate_tool_definition(definition)
            if definition.replacement_tool_id is not None and definition.replacement_tool_id not in self._by_id:
                raise ToolMissingError(
                    f"Replacement tool {definition.replacement_tool_id} is not registered with the deprecated tool"
                )
