"""Pluggable deterministic symbol resolution with a Python AST adapter."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import orjson

from infinitecontex.context_budget.estimation import normalize_text
from infinitecontex.task_context.models import (
    InventoryClassification,
    PathReferenceKind,
    ReferenceRequirement,
    RepositoryInventory,
    ResolvedSymbolMatch,
    SymbolKind,
    SymbolReference,
    SymbolResolution,
    SymbolResolutionOutcome,
)
from infinitecontex.task_context.paths import FrozenSource, PathResolver
from infinitecontex.task_context.policy import RepositoryResolutionPolicy


@dataclass(frozen=True)
class FrozenSymbolResolution:
    resolution: SymbolResolution
    sources: tuple[FrozenSource, ...] = ()


class SymbolResolver(Protocol):
    resolver_id: str
    resolver_version: int

    def supports(self, language: str) -> bool: ...

    def resolve(self, reference: SymbolReference) -> FrozenSymbolResolution: ...


@dataclass(frozen=True)
class _IndexedSymbol:
    path: str
    name: str
    qualified_name: str
    kind: SymbolKind
    start_line: int
    end_line: int
    decorators: tuple[str, ...]


@dataclass(frozen=True)
class _ParsedPythonFile:
    path: str
    content: str
    raw_hash: str
    symbols: tuple[_IndexedSymbol, ...]
    syntax_error: str | None = None
    stale_content: bool = False


class PythonAstSymbolResolver:
    resolver_id = "python-ast-symbol-resolver-v1"
    resolver_version = 1

    def __init__(
        self,
        repository_root: Path,
        inventory: RepositoryInventory,
        path_resolver: PathResolver,
        policy: RepositoryResolutionPolicy | None = None,
    ) -> None:
        self.root = repository_root.resolve(strict=True)
        self.inventory = inventory
        self.path_resolver = path_resolver
        self.policy = policy or RepositoryResolutionPolicy()
        self._cache: dict[str, _ParsedPythonFile] = {}

    def supports(self, language: str) -> bool:
        return language.strip().casefold() in {"python", "py"}

    def resolve(self, reference: SymbolReference) -> FrozenSymbolResolution:
        if not self.supports(reference.language):
            return FrozenSymbolResolution(
                SymbolResolution(
                    reference=reference,
                    outcome=SymbolResolutionOutcome.UNSUPPORTED_LANGUAGE,
                    errors=(f"No deterministic semantic resolver is available for language {reference.language!r}.",),
                )
            )
        eligible, early = self._eligible_paths(reference)
        if early is not None:
            return early
        matches: list[tuple[_IndexedSymbol, _ParsedPythonFile]] = []
        syntax_errors: list[str] = []
        stale_paths: list[str] = []
        for path in eligible:
            parsed = self._parse(path)
            if parsed.stale_content:
                stale_paths.append(path)
                continue
            if parsed.syntax_error:
                syntax_errors.append(f"{path}: {parsed.syntax_error}")
                continue
            for symbol in parsed.symbols:
                if self._matches(reference, symbol, parsed.path):
                    matches.append((symbol, parsed))
        matches.sort(key=lambda item: (item[0].path, item[0].start_line, item[0].qualified_name, item[0].kind))
        if len(matches) > self.policy.maximum_symbol_matches:
            return FrozenSymbolResolution(
                SymbolResolution(
                    reference=reference,
                    outcome=SymbolResolutionOutcome.AMBIGUOUS,
                    errors=(
                        "Symbol reference has more than "
                        f"{self.policy.maximum_symbol_matches} matches; add a file hint.",
                    ),
                )
            )
        if stale_paths:
            return FrozenSymbolResolution(
                SymbolResolution(
                    reference=reference,
                    outcome=SymbolResolutionOutcome.STALE_CONTENT,
                    errors=(f"Source changed after inventory: {', '.join(sorted(stale_paths))}.",),
                )
            )
        if not matches:
            outcome = (
                SymbolResolutionOutcome.SYNTAX_ERROR
                if syntax_errors and reference.file_hint
                else SymbolResolutionOutcome.MISSING
            )
            return FrozenSymbolResolution(
                SymbolResolution(
                    reference=reference,
                    outcome=outcome,
                    warnings=tuple(syntax_errors),
                    errors=("No exact Python AST symbol match was found.",),
                )
            )
        if len(matches) > 1:
            records, _ = self._records(matches)
            return FrozenSymbolResolution(
                SymbolResolution(
                    reference=reference,
                    outcome=SymbolResolutionOutcome.AMBIGUOUS,
                    matches=records,
                    errors=("Symbol reference is ambiguous; add a qualified name or file hint.",),
                )
            )
        records, sources = self._records(matches)
        record = records[0]
        if reference.expected_symbol_fingerprint and reference.expected_symbol_fingerprint != record.symbol_fingerprint:
            return FrozenSymbolResolution(
                SymbolResolution(
                    reference=reference,
                    outcome=SymbolResolutionOutcome.HASH_MISMATCH,
                    matches=records,
                    errors=("Expected symbol fingerprint does not match current source.",),
                )
            )
        return FrozenSymbolResolution(
            SymbolResolution(
                reference=reference,
                outcome=(
                    SymbolResolutionOutcome.RESOLVED_WITH_FILE_HINT
                    if reference.file_hint
                    else SymbolResolutionOutcome.RESOLVED_EXACT
                ),
                matches=records,
            ),
            sources,
        )

    def _eligible_paths(self, reference: SymbolReference) -> tuple[tuple[str, ...], FrozenSymbolResolution | None]:
        entries = tuple(
            entry
            for entry in self.inventory.entries
            if entry.path.endswith(".py") and entry.classification == InventoryClassification.TEXT
        )
        if reference.file_hint:
            normalized, error = self.path_resolver.normalize(reference.file_hint, self._file_kind())
            if error or normalized is None:
                return (), FrozenSymbolResolution(
                    SymbolResolution(
                        reference=reference,
                        outcome=SymbolResolutionOutcome.UNSAFE_SOURCE,
                        errors=(error or "Invalid file hint.",),
                    )
                )
            matches = tuple(entry.path for entry in entries if self._path_equal(entry.path, normalized))
            if not matches:
                return (), FrozenSymbolResolution(
                    SymbolResolution(
                        reference=reference,
                        outcome=SymbolResolutionOutcome.MISSING,
                        errors=("Python symbol file hint is not a readable inventory file.",),
                    )
                )
            return matches, None
        return tuple(entry.path for entry in entries), None

    def _parse(self, path: str) -> _ParsedPythonFile:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        raw = (self.root / Path(path)).read_bytes()
        text = normalize_text(raw.decode("utf-8"))
        raw_hash = hashlib.sha256(raw).hexdigest()
        inventoried = next(item for item in self.inventory.entries if item.path == path)
        if inventoried.content_hash is not None and inventoried.content_hash != raw_hash:
            parsed = _ParsedPythonFile(path, text, raw_hash, (), stale_content=True)
            self._cache[path] = parsed
            return parsed
        try:
            tree = ast.parse(text, filename=path)
        except (SyntaxError, RecursionError, ValueError) as exc:
            parsed = _ParsedPythonFile(path, text, raw_hash, (), f"{type(exc).__name__}: {exc}")
            self._cache[path] = parsed
            return parsed
        module = _module_name(path)
        symbols = [
            _IndexedSymbol(
                path,
                module.rsplit(".", 1)[-1],
                module,
                SymbolKind.MODULE,
                1,
                max(1, len(text.splitlines())),
                (),
            )
        ]
        stack: list[tuple[ast.AST, tuple[str, ...], bool]] = [(node, (), False) for node in reversed(tree.body)]
        while stack:
            node, parents, parent_is_class = stack.pop()
            indexed = _index_node(node, path, module, parents, parent_is_class)
            next_parents = parents
            next_parent_is_class = False
            if indexed is not None:
                symbols.append(indexed)
                next_parents = (*parents, indexed.name)
                next_parent_is_class = indexed.kind == SymbolKind.CLASS
            children = _definition_children(node)
            for child in reversed(children):
                stack.append((child, next_parents, next_parent_is_class))
        parsed = _ParsedPythonFile(path, text, raw_hash, tuple(symbols))
        self._cache[path] = parsed
        return parsed

    def _records(
        self, matches: list[tuple[_IndexedSymbol, _ParsedPythonFile]]
    ) -> tuple[tuple[ResolvedSymbolMatch, ...], tuple[FrozenSource, ...]]:
        records: list[ResolvedSymbolMatch] = []
        sources: list[FrozenSource] = []
        for symbol, parsed in matches:
            lines = parsed.content.splitlines(keepends=True)
            content = "".join(lines[symbol.start_line - 1 : symbol.end_line])
            source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            fingerprint = hashlib.sha256(
                orjson.dumps(
                    {
                        "path": symbol.path,
                        "qualified_name": symbol.qualified_name,
                        "kind": symbol.kind,
                        "start": symbol.start_line,
                        "end": symbol.end_line,
                        "source_hash": source_hash,
                        "resolver": self.resolver_id,
                        "version": self.resolver_version,
                    },
                    option=orjson.OPT_SORT_KEYS,
                )
            ).hexdigest()
            records.append(
                ResolvedSymbolMatch(
                    path=symbol.path,
                    qualified_name=symbol.qualified_name,
                    symbol_kind=symbol.kind,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    normalized_source_hash=source_hash,
                    symbol_fingerprint=fingerprint,
                    resolver_id=self.resolver_id,
                    resolver_version=self.resolver_version,
                    decorators=symbol.decorators,
                )
            )
            sources.append(
                FrozenSource(
                    path=symbol.path,
                    content=content,
                    full_content_hash=parsed.raw_hash,
                    frozen_content_hash=source_hash,
                    line_start=symbol.start_line,
                    line_end=symbol.end_line,
                    size_bytes=len(content.encode("utf-8")),
                )
            )
        return tuple(records), tuple(sources)

    @staticmethod
    def _matches(reference: SymbolReference, symbol: _IndexedSymbol, path: str) -> bool:
        if reference.symbol_kind != SymbolKind.UNKNOWN and reference.symbol_kind != symbol.kind:
            return False
        if reference.module_or_namespace and not symbol.qualified_name.startswith(reference.module_or_namespace):
            return False
        if reference.qualified_name:
            expected = reference.qualified_name
            if symbol.qualified_name != expected and not symbol.qualified_name.endswith(f".{expected}"):
                return False
        elif symbol.name != reference.symbol_name:
            return False
        if reference.line_start is not None and symbol.start_line != reference.line_start:
            return False
        if reference.line_end is not None and symbol.end_line != reference.line_end:
            return False
        return bool(path)

    def _path_equal(self, left: str, right: str) -> bool:
        return left.casefold() == right.casefold() if self.policy.path_case_policy == "insensitive" else left == right

    @staticmethod
    def _file_kind() -> PathReferenceKind:
        return PathReferenceKind.EXACT_FILE


class SymbolResolutionService:
    def __init__(self, resolvers: tuple[SymbolResolver, ...]) -> None:
        self.resolvers = resolvers

    def resolve(self, reference: SymbolReference) -> FrozenSymbolResolution:
        resolver = next((item for item in self.resolvers if item.supports(reference.language)), None)
        if resolver is None:
            return FrozenSymbolResolution(
                SymbolResolution(
                    reference=reference,
                    outcome=SymbolResolutionOutcome.UNSUPPORTED_LANGUAGE,
                    errors=(f"No resolver is registered for {reference.language}.",),
                )
            )
        return resolver.resolve(reference)


def symbol_reference_from_string(
    value: str,
    task_id: str,
    source_field: str,
    requirement: ReferenceRequirement = ReferenceRequirement.REQUIRED,
) -> SymbolReference:
    file_hint: str | None = None
    symbol = value.strip()
    if "::" in symbol:
        file_hint, symbol = symbol.split("::", 1)
    suffix = Path(file_hint).suffix.casefold() if file_hint else ""
    language = {".py": "python", ".cs": "csharp", ".js": "javascript", ".ts": "typescript"}.get(suffix, "python")
    qualified = symbol if "." in symbol else None
    return SymbolReference(
        original_reference=value,
        language=language,
        symbol_name=symbol.rsplit(".", 1)[-1],
        qualified_name=qualified,
        file_hint=file_hint,
        requirement=requirement,
        source_task_id=task_id,
        source_field=source_field,
    )


def _definition_children(node: ast.AST) -> list[ast.AST]:
    body = getattr(node, "body", None)
    return list(body) if isinstance(body, list) else []


def _index_node(
    node: ast.AST,
    path: str,
    module: str,
    parents: tuple[str, ...],
    parent_is_class: bool,
) -> _IndexedSymbol | None:
    name: str | None = None
    kind: SymbolKind | None = None
    decorators: tuple[str, ...] = ()
    if isinstance(node, ast.ClassDef):
        name = node.name
        kind = SymbolKind.TEST if node.name.startswith("Test") else SymbolKind.CLASS
        decorators = tuple(_decorator_name(item) for item in node.decorator_list)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        name = node.name
        decorators = tuple(_decorator_name(item) for item in node.decorator_list)
        if any(value.endswith("property") for value in decorators):
            kind = SymbolKind.PROPERTY
        elif name.startswith("test_"):
            kind = SymbolKind.TEST
        else:
            kind = SymbolKind.METHOD if parent_is_class else SymbolKind.FUNCTION
    elif isinstance(node, ast.Assign):
        target = node.targets[0] if node.targets else None
        if isinstance(target, ast.Name) and (target.id.isupper() or parent_is_class):
            name = target.id
            kind = SymbolKind.FIELD if parent_is_class else SymbolKind.CONSTANT
    elif isinstance(node, ast.AnnAssign):
        target = node.target
        if isinstance(target, ast.Name) and (target.id.isupper() or parent_is_class):
            name = target.id
            kind = SymbolKind.FIELD if parent_is_class else SymbolKind.CONSTANT
    if name is None or kind is None or not hasattr(node, "lineno"):
        return None
    local = ".".join((*parents, name))
    return _IndexedSymbol(
        path=path,
        name=name,
        qualified_name=f"{module}.{local}",
        kind=kind,
        start_line=node.lineno,
        end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
        decorators=decorators,
    )


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        values = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            values.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            values.append(current.id)
        return ".".join(reversed(values))
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return type(node).__name__


def _module_name(path: str) -> str:
    value = path.removesuffix(".py").replace("/", ".")
    return value.removesuffix(".__init__")
