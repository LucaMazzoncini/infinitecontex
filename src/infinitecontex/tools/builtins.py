"""Conservative data-only built-in declarations for future agent tools."""

# Compact declaration rows are intentionally kept as one catalog entry per line.
# ruff: noqa: E501

from __future__ import annotations

from datetime import UTC, datetime

from infinitecontex.planning.models import Capability
from infinitecontex.tools.execution_handlers import ReadHandlerRegistry
from infinitecontex.tools.fingerprints import definition_fingerprint, generate_tool_id
from infinitecontex.tools.models import (
    ApprovalClass,
    AvailabilityState,
    FieldType,
    ImplementationStatus,
    NormalizationRule,
    PathFieldSemantics,
    SchemaField,
    ScopeKind,
    ToolCategory,
    ToolDefinition,
    ToolEffects,
    ToolSchema,
    ToolScope,
)
from infinitecontex.tools.registry import ToolRegistry
from infinitecontex.tools.risk import derive_approval_class, derive_minimum_risk

_DECLARED_AT = datetime(2026, 7, 15, tzinfo=UTC)


def _field(name: str, semantics: PathFieldSemantics = PathFieldSemantics.NONE) -> SchemaField:
    return SchemaField(
        name=name,
        field_type=FieldType.STRING,
        description=f"Strict {name.replace('_', ' ')} input.",
        maximum_length=1000,
        path_semantics=semantics,
        normalization=(
            NormalizationRule.REPOSITORY_PATH if semantics != PathFieldSemantics.NONE else NormalizationRule.TRIM
        ),
    )


def make_tool_definition(
    canonical_name: str,
    *,
    display_name: str,
    category: ToolCategory,
    capabilities: tuple[Capability, ...],
    effects: ToolEffects,
    scopes: tuple[ToolScope, ...] = (),
    inputs: tuple[SchemaField, ...] = (),
    outputs: tuple[SchemaField, ...] = (),
    status: ImplementationStatus = ImplementationStatus.DECLARED_ONLY,
    availability: AvailabilityState = AvailabilityState.DEFINITION_AVAILABLE,
    description: str | None = None,
    version: str = "1.0.0",
    provenance: str = "InfiniteContext G1 built-in registry",
) -> ToolDefinition:
    risk = derive_minimum_risk(effects)
    provisional = ToolDefinition(
        tool_id=generate_tool_id(canonical_name, version),
        canonical_name=canonical_name,
        display_name=display_name,
        tool_version=version,
        provider_family="infinitecontex",
        description=description or f"Data-only declaration for {display_name.lower()}; no execution handler exists.",
        category=category,
        implementation_status=status,
        availability=availability,
        definition_fingerprint="0" * 64,
        provenance=provenance,
        created_at=_DECLARED_AT,
        updated_at=_DECLARED_AT,
        input_schema=ToolSchema(fields=inputs),
        output_schema=ToolSchema(fields=outputs),
        effects=effects,
        required_capabilities=tuple(sorted(capabilities, key=lambda item: item.value)),
        scopes=scopes,
        declared_risk=risk,
        derived_risk=risk,
        approval_class=ApprovalClass.NO_EXECUTION_AVAILABLE,
    )
    provisional = provisional.model_copy(update={"approval_class": derive_approval_class(provisional)})
    return provisional.model_copy(update={"definition_fingerprint": definition_fingerprint(provisional)})


def _path_contract(
    kind: ScopeKind, field: str, semantics: PathFieldSemantics, *, context: bool = True
) -> tuple[tuple[ToolScope, ...], tuple[SchemaField, ...]]:
    return (
        (ToolScope(kind=kind, input_field=field, requires_resolved_task_context=context),),
        (_field(field, semantics),),
    )


def builtin_definitions() -> tuple[ToolDefinition, ...]:
    read_scope, read_input = _path_contract(
        ScopeKind.EXPLICIT_PATH_READ, "path", PathFieldSemantics.REPOSITORY_RELATIVE
    )
    range_scope, range_input = _path_contract(
        ScopeKind.EXPLICIT_SOURCE_RANGE_READ, "path", PathFieldSemantics.REPOSITORY_SOURCE_RANGE
    )
    write_scope, write_input = _path_contract(ScopeKind.AFFECTED_SCOPE_WRITE, "path", PathFieldSemantics.AFFECTED_SCOPE)
    test_scope, test_input = _path_contract(ScopeKind.TEST_ONLY_WRITE, "path", PathFieldSemantics.TEST_PATH)
    docs_scope, docs_input = _path_contract(
        ScopeKind.DOCUMENTATION_ONLY_WRITE, "path", PathFieldSemantics.DOCUMENTATION_PATH
    )
    no_effect = ToolEffects(read_only=True)
    repo_read = ToolEffects(read_only=True, reads_repository=True)
    process = ToolEffects(runs_local_processes=True)
    definitions = [
        make_tool_definition(
            "repository.read-file",
            display_name="Read repository file",
            category=ToolCategory.FILE_READ,
            capabilities=(Capability.READ_REPOSITORY,),
            effects=repo_read,
            scopes=read_scope,
            inputs=read_input,
            status=ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION,
        ),
        make_tool_definition(
            "repository.read-source-range",
            display_name="Read repository source range",
            category=ToolCategory.FILE_READ,
            capabilities=(Capability.READ_REPOSITORY,),
            effects=repo_read,
            scopes=range_scope,
            inputs=range_input,
            status=ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION,
        ),
        make_tool_definition(
            "git.inspect-status-diff",
            display_name="Inspect Git status and diff",
            category=ToolCategory.GIT_INSPECTION,
            capabilities=(Capability.INSPECT_GIT,),
            effects=repo_read,
            scopes=(ToolScope(kind=ScopeKind.GIT_METADATA),),
        ),
        make_tool_definition(
            "plan.inspect",
            display_name="Inspect persisted plan",
            category=ToolCategory.TASK_PLAN_INSPECTION,
            capabilities=(),
            effects=no_effect,
        ),
        make_tool_definition(
            "context.inspect-task-analysis",
            display_name="Inspect task context analysis",
            category=ToolCategory.CONTEXT_INSPECTION,
            capabilities=(),
            effects=no_effect,
        ),
        make_tool_definition(
            "context.inspect-manifest",
            display_name="Inspect context manifest",
            category=ToolCategory.CONTEXT_INSPECTION,
            capabilities=(),
            effects=no_effect,
        ),
        make_tool_definition(
            "context.calculate-fit",
            display_name="Calculate deterministic context fit",
            category=ToolCategory.CONTEXT_INSPECTION,
            capabilities=(Capability.READ_REPOSITORY,),
            effects=repo_read,
            scopes=(ToolScope(kind=ScopeKind.REPOSITORY_WIDE_READ, requires_resolved_task_context=True),),
        ),
        make_tool_definition(
            "repository.list-inventory",
            display_name="List inventoried repository files",
            category=ToolCategory.REPOSITORY_READ,
            capabilities=(Capability.READ_REPOSITORY,),
            effects=repo_read,
            scopes=(ToolScope(kind=ScopeKind.REPOSITORY_WIDE_READ, requires_resolved_task_context=True),),
            status=ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION,
        ),
        make_tool_definition(
            "repository.search-paths",
            display_name="Search repository paths",
            category=ToolCategory.REPOSITORY_READ,
            capabilities=(Capability.READ_REPOSITORY,),
            effects=repo_read,
            scopes=(ToolScope(kind=ScopeKind.REPOSITORY_WIDE_READ, requires_resolved_task_context=True),),
            inputs=(_field("glob", PathFieldSemantics.REPOSITORY_RELATIVE),),
            status=ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION,
        ),
        make_tool_definition(
            "repository.search-literal",
            display_name="Search repository literal text",
            category=ToolCategory.REPOSITORY_READ,
            capabilities=(Capability.READ_REPOSITORY,),
            effects=repo_read,
            scopes=(ToolScope(kind=ScopeKind.REPOSITORY_WIDE_READ, requires_resolved_task_context=True),),
            inputs=(_field("query"), _field("glob", PathFieldSemantics.REPOSITORY_RELATIVE)),
            status=ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION,
        ),
        make_tool_definition(
            "repository.write-source",
            display_name="Write source file",
            category=ToolCategory.FILE_WRITE,
            capabilities=(Capability.WRITE_SOURCE_FILES,),
            effects=ToolEffects(writes_repository_files=True),
            scopes=write_scope,
            inputs=write_input,
        ),
        make_tool_definition(
            "repository.apply-source-patch",
            display_name="Apply structured source patch",
            category=ToolCategory.SOURCE_CODE_EDIT,
            capabilities=(Capability.WRITE_SOURCE_FILES,),
            effects=ToolEffects(writes_repository_files=True),
            scopes=write_scope,
            inputs=write_input,
            status=ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION,
        ),
        make_tool_definition(
            "repository.write-test",
            display_name="Write test file",
            category=ToolCategory.FILE_WRITE,
            capabilities=(Capability.WRITE_TESTS,),
            effects=ToolEffects(writes_repository_files=True),
            scopes=test_scope,
            inputs=test_input,
            status=ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION,
        ),
        make_tool_definition(
            "repository.write-documentation",
            display_name="Write documentation",
            category=ToolCategory.DOCUMENTATION_GENERATION,
            capabilities=(Capability.WRITE_DOCUMENTATION,),
            effects=ToolEffects(writes_repository_files=True),
            scopes=docs_scope,
            inputs=docs_input,
            status=ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION,
        ),
        make_tool_definition(
            "task.update-infctx-state",
            display_name="Update .infctx task state",
            category=ToolCategory.OTHER,
            capabilities=(Capability.MUTATE_TASK_STATUS,),
            effects=ToolEffects(writes_infctx=True, mutates_task_status=True),
            scopes=(ToolScope(kind=ScopeKind.INFCTX_ONLY_WRITE),),
        ),
        make_tool_definition(
            "plan.apply-approved-revision",
            display_name="Apply approved plan revision",
            category=ToolCategory.OTHER,
            capabilities=(Capability.MUTATE_PLANS, Capability.REQUEST_HUMAN_APPROVAL),
            effects=ToolEffects(writes_infctx=True, mutates_plans=True, requests_human_approval=True),
            scopes=(ToolScope(kind=ScopeKind.INFCTX_ONLY_WRITE),),
        ),
        make_tool_definition(
            "execution.run-tests",
            display_name="Run permitted test command",
            category=ToolCategory.TEST_EXECUTION,
            capabilities=(Capability.RUN_TESTS,),
            effects=process,
            status=ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION,
        ),
        make_tool_definition(
            "execution.run-build",
            display_name="Run permitted build command",
            category=ToolCategory.BUILD_EXECUTION,
            capabilities=(Capability.RUN_BUILD,),
            effects=process,
            status=ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION,
        ),
        make_tool_definition(
            "execution.run-static-analysis",
            display_name="Run static analysis",
            category=ToolCategory.STATIC_ANALYSIS,
            capabilities=(Capability.EXECUTE_COMMANDS,),
            effects=process,
            status=ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION,
        ),
        make_tool_definition(
            "execution.run-bounded-shell",
            display_name="Run bounded shell command",
            category=ToolCategory.SHELL_COMMAND,
            capabilities=(Capability.EXECUTE_COMMANDS,),
            effects=process,
        ),
        make_tool_definition(
            "git.stage-approved-files",
            display_name="Stage approved files",
            category=ToolCategory.GIT_MUTATION,
            capabilities=(Capability.CREATE_COMMITS,),
            effects=ToolEffects(modifies_git_index=True),
            scopes=(ToolScope(kind=ScopeKind.GIT_METADATA),),
        ),
        make_tool_definition(
            "git.create-commit",
            display_name="Create Git commit",
            category=ToolCategory.GIT_MUTATION,
            capabilities=(Capability.CREATE_COMMITS,),
            effects=ToolEffects(modifies_git_index=True, creates_commits=True),
            scopes=(ToolScope(kind=ScopeKind.GIT_METADATA),),
        ),
        make_tool_definition(
            "git.push-current-branch",
            display_name="Push current branch",
            category=ToolCategory.GIT_MUTATION,
            capabilities=(Capability.PUSH_COMMITS, Capability.USE_NETWORK),
            effects=ToolEffects(pushes_remotely=True, accesses_network=True),
            scopes=(
                ToolScope(kind=ScopeKind.GIT_METADATA),
                ToolScope(kind=ScopeKind.NETWORK_DESTINATION, declared_values=("configured-upstream",)),
            ),
        ),
    ]
    restricted = (
        (
            "network.request",
            "Network request",
            ToolCategory.NETWORK_ACCESS,
            (Capability.USE_NETWORK,),
            ToolEffects(accesses_network=True),
            (ToolScope(kind=ScopeKind.NETWORK_DESTINATION),),
        ),
        (
            "dependency.install",
            "Install dependency",
            ToolCategory.SHELL_COMMAND,
            (Capability.EXECUTE_COMMANDS, Capability.USE_NETWORK),
            ToolEffects(runs_local_processes=True, accesses_network=True, writes_repository_files=True),
            (ToolScope(kind=ScopeKind.AFFECTED_SCOPE_WRITE), ToolScope(kind=ScopeKind.NETWORK_DESTINATION)),
        ),
        (
            "filesystem.write-external",
            "Write outside repository",
            ToolCategory.FILE_WRITE,
            (Capability.WRITE_ARTIFACTS,),
            ToolEffects(accesses_paths_outside_repository=True, creates_artifacts=True),
            (ToolScope(kind=ScopeKind.EXTERNAL_FILESYSTEM),),
        ),
        (
            "filesystem.delete",
            "Destructive file deletion",
            ToolCategory.FILE_WRITE,
            (Capability.WRITE_SOURCE_FILES,),
            ToolEffects(writes_repository_files=True, deletes_data=True, irreversible=True),
            (ToolScope(kind=ScopeKind.AFFECTED_SCOPE_WRITE),),
        ),
        (
            "git.rewrite-history",
            "Rewrite Git history",
            ToolCategory.GIT_MUTATION,
            (Capability.CREATE_COMMITS,),
            ToolEffects(modifies_git_index=True, creates_commits=True, rewrites_git_history=True, irreversible=True),
            (ToolScope(kind=ScopeKind.GIT_METADATA),),
        ),
        (
            "git.force-push",
            "Force-push",
            ToolCategory.GIT_MUTATION,
            (Capability.PUSH_COMMITS, Capability.USE_NETWORK),
            ToolEffects(
                pushes_remotely=True,
                accesses_network=True,
                rewrites_git_history=True,
                irreversible=True,
            ),
            (
                ToolScope(kind=ScopeKind.GIT_METADATA),
                ToolScope(kind=ScopeKind.NETWORK_DESTINATION, declared_values=("configured-upstream",)),
            ),
        ),
        ("secrets.access", "Access secrets", ToolCategory.OTHER, (), ToolEffects(accesses_secrets=True), ()),
    )
    for name, display, category, capabilities, effects, scopes in restricted:
        definitions.append(
            make_tool_definition(
                name,
                display_name=display,
                category=category,
                capabilities=capabilities,
                effects=effects,
                scopes=scopes,
                status=ImplementationStatus.UNAVAILABLE,
                availability=AvailabilityState.PERMANENTLY_FORBIDDEN,
            )
        )
    return tuple(sorted(definitions, key=lambda item: (item.canonical_name, item.tool_version)))


def builtin_registry() -> ToolRegistry:
    return ToolRegistry(builtin_definitions())


def builtin_read_handler_registry(registry: ToolRegistry) -> ReadHandlerRegistry:
    """Bind only the five G2 read handlers to exact validated definitions."""
    from infinitecontex.tools.execution_handlers import (
        HandlerBinding,
        ReadHandlerRegistry,
        list_files,
        read_file,
        search_literal,
        search_paths,
    )

    rows = (
        ("repository.list-inventory", "list_inventory_v1", list_files),
        ("repository.read-file", "read_file_v1", read_file),
        ("repository.read-source-range", "read_source_range_v1", read_file),
        ("repository.search-paths", "search_paths_v1", search_paths),
        ("repository.search-literal", "search_literal_v1", search_literal),
    )
    return ReadHandlerRegistry(
        tuple(
            HandlerBinding(
                tool_id=definition.tool_id,
                tool_version=definition.tool_version,
                definition_fingerprint=definition.definition_fingerprint,
                handler_name=handler_name,
                handler=handler,
            )
            for canonical_name, handler_name, handler in rows
            for definition in (registry.get_by_name_version(canonical_name, "1.0.0"),)
        )
    )
