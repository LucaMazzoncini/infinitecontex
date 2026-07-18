"""Deterministic trusted G4 validation-command catalog and parameter expansion."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Literal, Mapping

from pydantic import BaseModel

from infinitecontex.task_context.models import PathReferenceKind, RepositoryInventory
from infinitecontex.task_context.paths import PathResolver
from infinitecontex.tools.validation_models import (
    CommandCategory,
    EnvironmentSummary,
    ExecutableIdentity,
    ParameterDefinition,
    ParameterKind,
    ValidationCommandDefinition,
)

_SAFE_ARGUMENT = re.compile(r"^[A-Za-z0-9_./\\:-]{1,1000}$")
_SENSITIVE = re.compile(r"(TOKEN|PASSWORD|PASSWD|SECRET|CREDENTIAL|API_KEY|AUTHORIZATION|SSH_AUTH|PROXY)", re.I)
_ALLOWED_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "PYTHONIOENCODING", "VIRTUAL_ENV")


def fingerprint_payload(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def command_fingerprint(value: ValidationCommandDefinition) -> str:
    payload = value.model_dump(mode="json", exclude={"fingerprint", "command_id"})
    return fingerprint_payload(payload)


def proposal_fingerprint(value: BaseModel) -> str:
    data = value.model_dump(mode="json", exclude={"semantic_fingerprint", "proposal_id", "created_at"})
    return fingerprint_payload(data)


def approval_fingerprint(value: BaseModel) -> str:
    data = value.model_dump(mode="json", exclude={"approval_fingerprint", "approval_id", "decided_at"})
    return fingerprint_payload(data)


def record_fingerprint(value: BaseModel) -> str:
    data = value.model_dump(mode="json", exclude={"record_fingerprint", "execution_id"})
    return fingerprint_payload(data)


def evidence_fingerprint(value: BaseModel) -> str:
    data = value.model_dump(mode="json", exclude={"fingerprint", "evidence_id"})
    return fingerprint_payload(data)


def _definition(
    name: str,
    display: str,
    category: CommandCategory,
    tool: str,
    module: Literal["pytest", "ruff", "mypy", "build"],
    fixed: tuple[str, ...],
    parameters: tuple[ParameterDefinition, ...] = (),
    transients: tuple[str, ...] = (),
) -> ValidationCommandDefinition:
    provisional = ValidationCommandDefinition(
        command_id="validation-command-" + "0" * 24,
        fingerprint="0" * 64,
        canonical_name=name,
        display_name=display,
        category=category,
        description=f"Trusted offline {display.lower()} profile.",
        tool_name=tool,
        module=module,
        fixed_arguments=fixed,
        parameters=parameters,
        allowed_environment_names=_ALLOWED_ENV,
        timeout_seconds=300,
        termination_grace_seconds=5,
        stdout_limit_bytes=2 * 1024 * 1024,
        stderr_limit_bytes=2 * 1024 * 1024,
        output_line_limit=50000,
        maximum_arguments=128,
        maximum_argument_length=1000,
        permitted_transient_prefixes=transients,
    )
    fp = command_fingerprint(provisional)
    return provisional.model_copy(update={"fingerprint": fp, "command_id": f"validation-command-{fp[:24]}"})


def builtin_commands() -> tuple[ValidationCommandDefinition, ...]:
    targets = ParameterDefinition(name="target", kind=ParameterKind.REPOSITORY_PATHS, maximum_items=64)
    return tuple(
        sorted(
            (
                _definition(
                    "python.pytest",
                    "Pytest suite",
                    CommandCategory.TEST,
                    "execution.run-tests",
                    "pytest",
                    (),
                    (targets,),
                    (".pytest_cache/",),
                ),
                _definition(
                    "python.ruff-format-check",
                    "Ruff formatting check",
                    CommandCategory.FORMAT,
                    "execution.run-static-analysis",
                    "ruff",
                    ("format", "--check", "."),
                    transients=(".ruff_cache/",),
                ),
                _definition(
                    "python.ruff-check",
                    "Ruff lint check",
                    CommandCategory.LINT,
                    "execution.run-static-analysis",
                    "ruff",
                    ("check", "."),
                    transients=(".ruff_cache/",),
                ),
                _definition(
                    "python.mypy-src",
                    "Mypy source check",
                    CommandCategory.STATIC_ANALYSIS,
                    "execution.run-static-analysis",
                    "mypy",
                    ("src",),
                    transients=(".mypy_cache/",),
                ),
                _definition(
                    "python.build",
                    "Python package build",
                    CommandCategory.BUILD,
                    "execution.run-build",
                    "build",
                    (),
                    transients=("dist/", "build/"),
                ),
            ),
            key=lambda item: (item.canonical_name, item.version),
        )
    )


class ValidationCommandRegistry:
    def __init__(self, definitions: Iterable[ValidationCommandDefinition] | None = None) -> None:
        values = builtin_commands() if definitions is None else tuple(definitions)
        self._by_id = {item.command_id: item for item in values}
        self._by_name = {item.canonical_name: item for item in values}
        if len(self._by_id) != len(values) or len(self._by_name) != len(values):
            raise ValueError("Duplicate validation command definition")
        for item in values:
            if (
                command_fingerprint(item) != item.fingerprint
                or item.command_id != f"validation-command-{item.fingerprint[:24]}"
            ):
                raise ValueError("Malformed validation command definition")
            if item.network_allowed or item.interactive_input:
                raise ValueError("Unsafe validation command definition")
        self.definitions = tuple(sorted(values, key=lambda item: item.command_id))
        self.fingerprint = fingerprint_payload([item.fingerprint for item in self.definitions])

    def get(self, value: str) -> ValidationCommandDefinition:
        try:
            return self._by_id.get(value) or self._by_name[value]
        except KeyError as exc:
            raise ValueError(f"Validation command {value!r} was not found") from exc


def resolve_executable() -> ExecutableIdentity:
    path = Path(sys.executable).resolve(strict=True)
    if path.suffix.lower() in {".bat", ".cmd", ".ps1", ".sh"}:
        raise ValueError("Shell wrappers are not trusted executables")
    data = path.read_bytes()
    return ExecutableIdentity(resolved_path=str(path), sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))


def verify_executable(identity: ExecutableIdentity) -> None:
    current = resolve_executable()
    if current != identity:
        raise ValueError("Executable identity changed after proposal creation")


def build_environment(source: Mapping[str, str]) -> tuple[dict[str, str], EnvironmentSummary]:
    safe: dict[str, str] = {}
    removed: list[str] = []
    for name, value in source.items():
        if _SENSITIVE.search(name):
            removed.append(name)
        elif name in _ALLOWED_ENV:
            safe[name] = value
    safe["PYTHONIOENCODING"] = "utf-8"
    summary = EnvironmentSummary(
        names=tuple(sorted(safe)),
        fingerprint=fingerprint_payload(sorted(safe.items())),
        removed_sensitive_names=tuple(sorted(removed)),
    )
    return safe, summary


def build_arguments(
    definition: ValidationCommandDefinition,
    parameters: Mapping[str, tuple[str, ...]],
    root: Path,
    inventory: RepositoryInventory,
) -> tuple[tuple[str, ...], tuple[tuple[str, tuple[str, ...]], ...]]:
    known = {item.name: item for item in definition.parameters}
    if set(parameters) - set(known):
        raise ValueError(f"Unknown parameter: {sorted(set(parameters) - set(known))[0]}")
    expanded = ["-m", definition.module, *definition.fixed_arguments]
    normalized: list[tuple[str, tuple[str, ...]]] = []
    resolver = PathResolver(root, inventory)
    for name in sorted(parameters):
        spec, values = known[name], parameters[name]
        if len(values) > spec.maximum_items:
            raise ValueError(f"Parameter {name} exceeds its item limit")
        cooked: list[str] = []
        for value in values:
            if "\x00" in value or any(ord(char) < 32 for char in value) or not _SAFE_ARGUMENT.fullmatch(value):
                raise ValueError(f"Parameter {name} contains unsafe characters")
            if value.startswith("-"):
                raise ValueError(f"Parameter {name} cannot inject options")
            if spec.kind == ParameterKind.REPOSITORY_PATHS:
                normalized_value, error = resolver.normalize(value, PathReferenceKind.EXACT_FILE)
                if error or normalized_value is None:
                    raise ValueError(f"Repository target is unsafe: {error}")
                value = normalized_value
                if not (root / value).exists():
                    raise ValueError(f"Repository target {value!r} does not exist")
            elif spec.kind == ParameterKind.ENUM and value not in spec.enum_values:
                raise ValueError(f"Parameter {name} has an unsupported value")
            elif spec.kind == ParameterKind.BOUNDED_INTEGER:
                number = int(value)
                if (
                    spec.minimum is not None
                    and number < spec.minimum
                    or spec.maximum is not None
                    and number > spec.maximum
                ):
                    raise ValueError(f"Parameter {name} is outside its bounds")
            cooked.append(value)
        if spec.flag:
            expanded.append(spec.flag)
        expanded.extend(cooked)
        normalized.append(
            (name, tuple(sorted(cooked)) if spec.kind == ParameterKind.REPOSITORY_PATHS else tuple(cooked))
        )
    if len(expanded) > definition.maximum_arguments or any(
        len(item) > definition.maximum_argument_length for item in expanded
    ):
        raise ValueError("Expanded arguments exceed command limits")
    return tuple(expanded), tuple(normalized)
