from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from infinitecontex.task_context.repository import RepositoryInventoryService
from infinitecontex.tools.builtins import builtin_registry
from infinitecontex.tools.execution_service import NoCommandGitStateProvider
from infinitecontex.tools.models import ImplementationStatus
from infinitecontex.tools.validation_definitions import (
    ValidationCommandRegistry,
    build_arguments,
    build_environment,
    command_fingerprint,
    resolve_executable,
    verify_executable,
)
from infinitecontex.tools.validation_runner import BoundedProcessRunner


def _inventory(root: Path):
    return RepositoryInventoryService(git_provider=NoCommandGitStateProvider()).build(root)


def test_catalog_is_deterministic_allowlisted_and_promoted() -> None:
    first = ValidationCommandRegistry()
    second = ValidationCommandRegistry(reversed(first.definitions))
    assert first.fingerprint == second.fingerprint
    assert [item.command_id for item in first.definitions] == [item.command_id for item in second.definitions]
    assert {item.module for item in first.definitions} == {"pytest", "ruff", "mypy", "build"}
    assert all(not item.network_allowed and not item.interactive_input for item in first.definitions)
    assert all(item.fingerprint == command_fingerprint(item) for item in first.definitions)
    tools = builtin_registry()
    for name in ("execution.run-tests", "execution.run-build", "execution.run-static-analysis"):
        assert (
            tools.get_by_name_version(name, "1.0.0").implementation_status
            == ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION
        )
    assert (
        tools.get_by_name_version("execution.run-bounded-shell", "1.0.0").implementation_status
        == ImplementationStatus.DECLARED_ONLY
    )


def test_argument_schema_normalizes_targets_and_rejects_injection(tmp_path: Path) -> None:
    target = tmp_path / "tests" / "test_one.py"
    target.parent.mkdir()
    target.write_text("pass\n", encoding="utf-8")
    definition = ValidationCommandRegistry().get("python.pytest")
    arguments, parameters = build_arguments(
        definition, {"target": ("tests\\test_one.py",)}, tmp_path, _inventory(tmp_path)
    )
    assert arguments == ("-m", "pytest", "tests/test_one.py")
    assert parameters == (("target", ("tests/test_one.py",)),)
    with pytest.raises(ValueError, match="inject"):
        build_arguments(definition, {"target": ("--collect-only",)}, tmp_path, _inventory(tmp_path))
    with pytest.raises(ValueError, match="Unknown"):
        build_arguments(definition, {"command": ("whoami",)}, tmp_path, _inventory(tmp_path))
    with pytest.raises(ValueError):
        build_arguments(definition, {"target": ("../outside.py",)}, tmp_path, _inventory(tmp_path))


def test_maximum_target_and_argument_limits(tmp_path: Path) -> None:
    definition = ValidationCommandRegistry().get("python.pytest")
    values = []
    for index in range(64):
        item = tmp_path / f"test_{index}.py"
        item.write_text("pass\n", encoding="utf-8")
        values.append(item.name)
    arguments, _ = build_arguments(definition, {"target": tuple(reversed(values))}, tmp_path, _inventory(tmp_path))
    assert len(arguments) == 66
    with pytest.raises(ValueError, match="item limit"):
        build_arguments(definition, {"target": tuple(values + [values[0]])}, tmp_path, _inventory(tmp_path))


def test_environment_is_minimal_deterministic_and_secret_free(monkeypatch: pytest.MonkeyPatch) -> None:
    source = {"PATH": "safe", "SYSTEMROOT": "root", "API_TOKEN": "secret", "PASSWORD": "secret", "OTHER": "x"}
    safe, summary = build_environment(source)
    assert safe == {"PATH": "safe", "SYSTEMROOT": "root", "PYTHONIOENCODING": "utf-8"}
    assert summary.names == ("PATH", "PYTHONIOENCODING", "SYSTEMROOT")
    assert summary.removed_sensitive_names == ("API_TOKEN", "PASSWORD")
    assert build_environment(dict(reversed(tuple(source.items()))))[1].fingerprint == summary.fingerprint


def test_executable_identity_is_current_interpreter() -> None:
    identity = resolve_executable()
    assert Path(identity.resolved_path) == Path(sys.executable).resolve()
    assert identity.size_bytes > 0
    verify_executable(identity)


def test_runner_uses_no_shell_closed_stdin_and_separate_streams(tmp_path: Path) -> None:
    runner = BoundedProcessRunner()
    outcome = runner.run(
        sys.executable,
        ("-c", "import os,sys; print(os.getcwd()); print(sys.stdin.read()); print('err', file=sys.stderr)"),
        cwd=tmp_path,
        environment={"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PYTHONIOENCODING": "utf-8"},
        timeout_seconds=5,
        grace_seconds=1,
        stdout_limit=4096,
        stderr_limit=4096,
        line_limit=100,
    )
    assert outcome.exit_code == 0 and not outcome.timed_out
    assert str(tmp_path) in outcome.stdout.preview
    assert "err" in outcome.stderr.preview


def test_runner_bounds_and_redacts_output(tmp_path: Path) -> None:
    outcome = BoundedProcessRunner().run(
        sys.executable,
        ("-c", "print('token=supersecret'); print('x'*10000)"),
        cwd=tmp_path,
        environment={"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PYTHONIOENCODING": "utf-8"},
        timeout_seconds=5,
        grace_seconds=1,
        stdout_limit=1024,
        stderr_limit=1024,
        line_limit=100,
    )
    assert outcome.output_limit_exceeded and outcome.stdout.truncated
    assert outcome.stdout.byte_count > len(outcome.stdout.preview.encode())
    assert "[REDACTED]" in outcome.stdout.preview and "supersecret" not in outcome.stdout.preview


def test_runner_classifies_timeout_and_terminates(tmp_path: Path) -> None:
    outcome = BoundedProcessRunner().run(
        sys.executable,
        ("-c", "import time; print('started', flush=True); time.sleep(30)"),
        cwd=tmp_path,
        environment={"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PYTHONIOENCODING": "utf-8"},
        timeout_seconds=1,
        grace_seconds=1,
        stdout_limit=4096,
        stderr_limit=4096,
        line_limit=100,
    )
    assert outcome.timed_out
    assert "started" in outcome.stdout.preview
