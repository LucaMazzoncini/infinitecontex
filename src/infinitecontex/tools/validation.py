"""Fail-closed registration validation for strict data-only definitions."""

from __future__ import annotations

from infinitecontex.planning.models import Capability
from infinitecontex.tools.capabilities import normalize_capabilities
from infinitecontex.tools.errors import (
    ContradictoryEffectsError,
    InvalidScopeSemanticsError,
    MalformedToolDefinitionError,
)
from infinitecontex.tools.fingerprints import definition_fingerprint, generate_tool_id
from infinitecontex.tools.models import (
    AvailabilityState,
    ImplementationStatus,
    PathFieldSemantics,
    ScopeKind,
    ToolDefinition,
)
from infinitecontex.tools.risk import derive_approval_class, derive_minimum_risk, risk_rank


def validate_tool_definition(definition: ToolDefinition) -> ToolDefinition:
    expected_id = generate_tool_id(definition.canonical_name, definition.tool_version)
    if definition.tool_id != expected_id:
        raise MalformedToolDefinitionError("Tool ID does not match canonical name and version")
    expected_fingerprint = definition_fingerprint(definition)
    if definition.definition_fingerprint != expected_fingerprint:
        raise MalformedToolDefinitionError("Tool definition fingerprint does not match canonical content")
    normalize_capabilities(definition.required_capabilities)
    effects = definition.effects
    mutations = any(
        (
            effects.writes_repository_files,
            effects.writes_infctx,
            effects.modifies_git_index,
            effects.creates_commits,
            effects.pushes_remotely,
            effects.rewrites_git_history,
            effects.deletes_data,
            effects.runs_local_processes,
            effects.accesses_network,
            effects.accesses_paths_outside_repository,
            effects.invokes_model,
            effects.mutates_plans,
            effects.mutates_task_status,
            effects.creates_artifacts,
            effects.accesses_secrets,
            effects.requires_elevated_privileges,
            effects.irreversible,
        )
    )
    if effects.read_only and mutations:
        raise ContradictoryEffectsError("A read-only tool cannot declare mutation, execution, or external effects")
    if effects.creates_commits and not effects.modifies_git_index:
        raise ContradictoryEffectsError("Commit creation must declare Git index mutation")
    if effects.pushes_remotely and not effects.accesses_network:
        raise ContradictoryEffectsError("Remote push must declare its network effect")
    capabilities = set(definition.required_capabilities)
    if effects.accesses_network and not capabilities & {
        Capability.USE_NETWORK,
        Capability.PUSH_COMMITS,
    }:
        raise ContradictoryEffectsError("Network effects require an explicit network or push capability")
    if effects.creates_commits and Capability.CREATE_COMMITS not in capabilities:
        raise ContradictoryEffectsError("Commit effects require the create_commits capability")
    if effects.pushes_remotely and Capability.PUSH_COMMITS not in capabilities:
        raise ContradictoryEffectsError("Push effects require the push_commits capability")
    if definition.execution_handler is not None:
        raise MalformedToolDefinitionError("G1 tool definitions cannot contain execution handlers")
    if definition.implementation_status == ImplementationStatus.DECLARED_ONLY and definition.execution_handler:
        raise MalformedToolDefinitionError("Declared-only tools cannot claim handlers")
    if definition.replacement_tool_id == definition.tool_id:
        raise MalformedToolDefinitionError("A deprecated tool cannot replace itself")
    if definition.deprecated and definition.replacement_tool_id is None:
        raise MalformedToolDefinitionError("Deprecated tools require a replacement tool ID")
    if definition.availability == AvailabilityState.PERMANENTLY_FORBIDDEN and not mutations:
        raise MalformedToolDefinitionError("Permanently forbidden tools must declare the hazardous effect")
    floor = derive_minimum_risk(effects)
    if risk_rank(definition.declared_risk) < risk_rank(floor) or definition.derived_risk != floor:
        raise MalformedToolDefinitionError(f"Tool risk understates the deterministic {floor.name.lower()} floor")
    if definition.approval_class != derive_approval_class(definition):
        raise MalformedToolDefinitionError("Approval class does not match deterministic effects policy")
    _validate_scopes(definition)
    return definition


def _validate_scopes(definition: ToolDefinition) -> None:
    fields = {field.name: field for field in definition.input_schema.fields}
    for scope in definition.scopes:
        if scope.input_field is not None and scope.input_field not in fields:
            raise InvalidScopeSemanticsError(f"Scope input field {scope.input_field!r} is not declared")
        if scope.input_field is not None:
            path_semantics = fields[scope.input_field].path_semantics
            if path_semantics == PathFieldSemantics.NONE:
                raise InvalidScopeSemanticsError("Scope input fields require explicit path semantics")
        if scope.kind == ScopeKind.NONE and (scope.input_field or scope.declared_values):
            raise InvalidScopeSemanticsError("A no-scope declaration cannot contain values")
        for value in scope.declared_values:
            normalized = value.replace("\\", "/")
            if normalized.startswith(("/", "//")) or ":/" in normalized or ".." in normalized.split("/"):
                raise InvalidScopeSemanticsError(f"Unsafe declared scope {value!r}")
    scope_kinds = {scope.kind for scope in definition.scopes}
    if definition.effects.reads_repository and not scope_kinds & {
        ScopeKind.REPOSITORY_WIDE_READ,
        ScopeKind.EXPLICIT_PATH_READ,
        ScopeKind.EXPLICIT_SOURCE_RANGE_READ,
        ScopeKind.GIT_METADATA,
    }:
        raise InvalidScopeSemanticsError("Repository reads require explicit read scope semantics")
    if definition.effects.writes_repository_files and not scope_kinds & {
        ScopeKind.AFFECTED_SCOPE_WRITE,
        ScopeKind.TEST_ONLY_WRITE,
        ScopeKind.DOCUMENTATION_ONLY_WRITE,
    }:
        raise InvalidScopeSemanticsError("Repository writes require bounded write scope semantics")
