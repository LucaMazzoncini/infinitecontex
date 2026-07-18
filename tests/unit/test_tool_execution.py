from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import orjson
import pytest

from infinitecontex.task_context.policy import RepositoryResolutionPolicy
from infinitecontex.task_context.repository import RepositoryInventoryService
from infinitecontex.tools.builtins import builtin_read_handler_registry, builtin_registry
from infinitecontex.tools.execution_fingerprints import invocation_fingerprint
from infinitecontex.tools.execution_gateway import ReadOnlyExecutionGateway
from infinitecontex.tools.execution_handlers import HandlerBinding, ReadHandlerContext, ReadHandlerRegistry, list_files
from infinitecontex.tools.execution_models import (
    AuthorizationSource,
    CallerType,
    ExecutionStatus,
    InvocationInput,
    OperationKind,
)
from infinitecontex.tools.execution_service import NoCommandGitStateProvider, RepositoryReadExecutionService
from infinitecontex.tools.execution_store import ToolExecutionStore
from infinitecontex.tools.sensitive import SensitivePathPolicy

NOW = datetime(2026, 7, 16, 12, tzinfo=UTC)


def _write_repo(root: Path) -> None:
    (root / "src").mkdir()
    (root / "docs").mkdir()
    (root / "src" / "app.py").write_text("Alpha beta\nalpha TODO\nfinal\n", encoding="utf-8")
    (root / "src" / "bom.py").write_bytes(b"\xef\xbb\xbfOne\r\nTwo\r\n")
    (root / "docs" / "guide.md").write_text("Alpha guide\n", encoding="utf-8")
    (root / "empty.txt").write_text("", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"a\0b")
    (root / "invalid.txt").write_bytes(b"\xff\xfe")
    (root / ".env").write_text("TOKEN=secret", encoding="utf-8")


def _service(root: Path) -> RepositoryReadExecutionService:
    registry = builtin_registry()
    sensitive = SensitivePathPolicy()
    inventory = RepositoryInventoryService(
        git_provider=NoCommandGitStateProvider(),
        additional_path_filter=sensitive.permits,
        clock=lambda: NOW,
    )
    gateway = ReadOnlyExecutionGateway(
        registry,
        builtin_read_handler_registry(registry),
        ToolExecutionStore(root / ".infctx" / "tool-executions"),
        sensitive_policy=sensitive,
        clock=lambda: NOW,
    )
    return RepositoryReadExecutionService(
        registry,
        gateway,
        inventory_service=inventory,
        sensitive_policy=sensitive,
        clock=lambda: NOW,
    )


def test_read_utf8_bom_range_empty_pagination_and_records(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    service = _service(tmp_path)
    first = service.execute_human(
        tmp_path,
        "repository.read-file",
        InvocationInput(operation=OperationKind.READ_FILE, path="src\\app.py", limit=2),
    )
    assert first.result.status == ExecutionStatus.PARTIAL
    assert first.result.content == "Alpha beta\nalpha TODO\n"
    assert first.result.next_range == (3, 3) and first.result.encoding == "utf-8"
    continued = service.execute_human(
        tmp_path,
        "repository.read-file",
        InvocationInput(operation=OperationKind.READ_FILE, path="src/app.py", offset=2, limit=2),
    )
    assert continued.result.content == "final\n" and not continued.result.has_more
    bom = service.execute_human(
        tmp_path,
        "repository.read-source-range",
        InvocationInput(operation=OperationKind.READ_RANGE, path="src/bom.py", start_line=2, end_line=2),
    )
    assert bom.result.encoding == "utf-8-sig" and bom.result.content == "Two\n"
    empty = service.execute_human(
        tmp_path,
        "repository.read-file",
        InvocationInput(operation=OperationKind.READ_FILE, path="empty.txt"),
    )
    assert empty.result.content == "" and empty.result.line_count == 0
    record = orjson.loads((tmp_path / ".infctx" / "tool-executions" / f"{first.record.execution_id}.json").read_bytes())
    assert "content" not in record and "snippet" not in record and first.record.content_returned


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("missing.txt", ExecutionStatus.MISSING_PATH),
        ("binary.bin", ExecutionStatus.BINARY_FILE),
        ("invalid.txt", ExecutionStatus.UNSUPPORTED_ENCODING),
        (".git/config", ExecutionStatus.FORBIDDEN_PATH),
        (".infctx/config.json", ExecutionStatus.FORBIDDEN_PATH),
        (".env", ExecutionStatus.SENSITIVE_PATH),
    ],
)
def test_read_denials_are_explicit_and_content_free(tmp_path: Path, path: str, status: ExecutionStatus) -> None:
    _write_repo(tmp_path)
    envelope = _service(tmp_path).execute_human(
        tmp_path,
        "repository.read-file",
        InvocationInput(operation=OperationKind.READ_FILE, path=path),
    )
    assert envelope.result.status == status and envelope.result.content is None
    assert not envelope.record.content_returned


def test_traversal_large_and_changed_file_fail_closed(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    service = _service(tmp_path)
    with pytest.raises(Exception, match="traversal"):
        service.execute_human(
            tmp_path,
            "repository.read-file",
            InvocationInput(operation=OperationKind.READ_FILE, path="../outside.txt"),
        )
    (tmp_path / "large.txt").write_text("x" * 20, encoding="utf-8")
    service.inventory_service = RepositoryInventoryService(
        RepositoryResolutionPolicy(maximum_source_file_bytes=10),
        NoCommandGitStateProvider(),
        clock=lambda: NOW,
        additional_path_filter=SensitivePathPolicy().permits,
    )
    assert (
        service.execute_human(
            tmp_path,
            "repository.read-file",
            InvocationInput(operation=OperationKind.READ_FILE, path="large.txt"),
        ).result.status
        == ExecutionStatus.FILE_TOO_LARGE
    )
    service = _service(tmp_path)
    inventory = service.inventory_service.build(tmp_path)
    definition = service.registry.get_by_name_version("repository.read-file", "1.0.0")
    inputs = InvocationInput(operation=OperationKind.READ_FILE, path="src/app.py")
    invocation = service._invocation(
        definition.tool_id,
        definition.tool_version,
        inventory,
        inputs,
        CallerType.HUMAN_CLI,
        AuthorizationSource.EXPLICIT_HUMAN_CLI,
    )
    grant = service._grant(invocation, definition.definition_fingerprint)
    (tmp_path / "src" / "app.py").write_text("changed\n", encoding="utf-8")
    assert (
        service.gateway.execute(invocation, grant, inventory, tmp_path).result.status == ExecutionStatus.STALE_SNAPSHOT
    )


def test_literal_search_is_bounded_literal_ordered_and_continuable(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    service = _service(tmp_path)
    exact = service.execute_human(
        tmp_path,
        "repository.search-literal",
        InvocationInput(
            operation=OperationKind.SEARCH_LITERAL,
            query="alpha",
            ignore_case=True,
            maximum_matches=2,
            context_lines=1,
        ),
    )
    assert [(item.path, item.line, item.column) for item in exact.result.matches] == [
        ("docs/guide.md", 1, 1),
        ("src/app.py", 1, 1),
    ]
    assert exact.result.has_more and exact.result.continuation_offset == 2
    continued = service.execute_human(
        tmp_path,
        "repository.search-literal",
        InvocationInput(
            operation=OperationKind.SEARCH_LITERAL,
            query="alpha",
            ignore_case=True,
            maximum_matches=2,
            offset=2,
        ),
    )
    assert [(item.path, item.line) for item in continued.result.matches] == [("src/app.py", 2)]
    literal = service.execute_human(
        tmp_path,
        "repository.search-literal",
        InvocationInput(operation=OperationKind.SEARCH_LITERAL, query="(.+)+$"),
    )
    assert literal.result.status == ExecutionStatus.NO_MATCHES
    assert "alpha" not in ToolExecutionStore.serialize(exact.record).decode().lower()


def test_listing_path_filter_pagination_and_reverse_stability(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    service = _service(tmp_path)
    first = service.execute_human(
        tmp_path,
        "repository.list-inventory",
        InvocationInput(operation=OperationKind.LIST_FILES, limit=2),
    )
    second = service.execute_human(
        tmp_path,
        "repository.list-inventory",
        InvocationInput(operation=OperationKind.LIST_FILES, limit=2, offset=2),
    )
    assert first.result.has_more and set(item.path for item in first.result.files).isdisjoint(
        item.path for item in second.result.files
    )
    filtered = service.execute_human(
        tmp_path,
        "repository.search-paths",
        InvocationInput(operation=OperationKind.SEARCH_PATHS, glob="src/**/*.py", suffix=".py"),
    )
    assert all(item.path.startswith("src/") and item.path.endswith(".py") for item in filtered.result.files)
    inventory = service.inventory_service.build(tmp_path)
    reverse = inventory.model_copy(update={"entries": tuple(reversed(inventory.entries))})
    inputs = InvocationInput(operation=OperationKind.LIST_FILES, limit=100)
    assert (
        list_files(inputs, ReadHandlerContext(tmp_path, inventory)).files
        == list_files(inputs, ReadHandlerContext(tmp_path, reverse)).files
    )


def test_gateway_rejections_never_call_handler(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    service = _service(tmp_path)
    inventory = service.inventory_service.build(tmp_path)
    definition = service.registry.get_by_name_version("repository.read-file", "1.0.0")
    inputs = InvocationInput(operation=OperationKind.READ_FILE, path="src/app.py")
    invocation = service._invocation(
        definition.tool_id,
        definition.tool_version,
        inventory,
        inputs,
        CallerType.HUMAN_CLI,
        AuthorizationSource.EXPLICIT_HUMAN_CLI,
        correlation_id="stable",
    )
    assert invocation.invocation_fingerprint == invocation_fingerprint(invocation)
    grant = service._grant(invocation, definition.definition_fingerprint)
    called = 0

    def spy(_inputs: InvocationInput, _context: ReadHandlerContext):
        nonlocal called
        called += 1
        raise AssertionError

    binding = HandlerBinding(
        definition.tool_id,
        definition.tool_version,
        definition.definition_fingerprint,
        "spy",
        spy,
    )
    gateway = ReadOnlyExecutionGateway(
        service.registry,
        ReadHandlerRegistry((binding,)),
        ToolExecutionStore(tmp_path / ".infctx" / "rejections"),
        clock=lambda: NOW,
    )
    tampered = invocation.model_copy(update={"registry_fingerprint": "0" * 64})
    assert gateway.execute(tampered, grant, inventory, tmp_path).result.status == ExecutionStatus.REJECTED
    changed_grant = grant.model_copy(update={"repository_snapshot_fingerprint": "0" * 64})
    assert gateway.execute(invocation, changed_grant, inventory, tmp_path).result.status == ExecutionStatus.REJECTED
    stale = inventory.model_copy(
        update={"snapshot": inventory.snapshot.model_copy(update={"semantic_fingerprint": "f" * 64})}
    )
    assert gateway.execute(invocation, grant, stale, tmp_path).result.status == ExecutionStatus.STALE_SNAPSHOT
    assert called == 0

    no_handler_gateway = ReadOnlyExecutionGateway(
        service.registry,
        ReadHandlerRegistry(()),
        ToolExecutionStore(tmp_path / ".infctx" / "missing-handler"),
        clock=lambda: NOW,
    )
    missing = no_handler_gateway.execute(invocation, grant, inventory, tmp_path)
    assert missing.result.error_codes == ("missing_handler",)

    mismatched_binding = binding.__class__(
        definition.tool_id,
        definition.tool_version,
        "0" * 64,
        "mismatched",
        spy,
    )
    mismatched_gateway = ReadOnlyExecutionGateway(
        service.registry,
        ReadHandlerRegistry((mismatched_binding,)),
        ToolExecutionStore(tmp_path / ".infctx" / "mismatched-handler"),
        clock=lambda: NOW,
    )
    mismatch = mismatched_gateway.execute(invocation, grant, inventory, tmp_path)
    assert mismatch.result.error_codes == ("handler_fingerprint_mismatch",)

    unsupported = invocation.model_copy(update={"tool_id": "tool-" + "f" * 24})
    unsupported_result = gateway.execute(unsupported, grant, inventory, tmp_path)
    assert unsupported_result.result.status == ExecutionStatus.REJECTED
    assert called == 0
    for name in ("repository.write-source", "network.request", "execution.run-bounded-shell"):
        blocked = service.registry.get_by_name_version(name, "1.0.0")
        blocked_invocation = service._invocation(
            blocked.tool_id,
            blocked.tool_version,
            inventory,
            inputs,
            CallerType.HUMAN_CLI,
            AuthorizationSource.EXPLICIT_HUMAN_CLI,
        )
        blocked_grant = service._grant(blocked_invocation, blocked.definition_fingerprint)
        assert (
            gateway.execute(blocked_invocation, blocked_grant, inventory, tmp_path).result.status
            == ExecutionStatus.REJECTED
        )
    assert called == 0


def test_ten_thousand_file_listing_and_bounded_search(tmp_path: Path) -> None:
    root = tmp_path / "scale"
    root.mkdir()
    for index in range(10_000):
        (root / f"file-{index:05d}.txt").write_text(f"value {index}\n", encoding="utf-8")
    service = _service(root)
    listed = service.execute_human(
        root,
        "repository.list-inventory",
        InvocationInput(operation=OperationKind.LIST_FILES, limit=1000),
    )
    assert len(listed.result.files) == 1000 and listed.result.has_more
    searched = service.execute_human(
        root,
        "repository.search-literal",
        InvocationInput(
            operation=OperationKind.SEARCH_LITERAL,
            query="value",
            maximum_matches=25,
            maximum_files=100,
            maximum_bytes=100_000,
        ),
    )
    assert len(searched.result.matches) == 25 and searched.result.has_more
    assert searched.result.files_scanned <= 100 and searched.result.bytes_scanned <= 100_000
