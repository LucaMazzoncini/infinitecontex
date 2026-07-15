from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

from pytest import MonkeyPatch
from test_task_context_cli import _import, _plan_file, _profile
from typer.testing import CliRunner

import infinitecontex.cli as cli_module
from infinitecontex.cli import app
from infinitecontex.planning.models import PlannerProvenance, TaskStatus
from infinitecontex.tools.builtins import builtin_registry


def _import_plan(runner: CliRunner, repo: Path, tmp_path: Path) -> tuple[str, str]:
    path = tmp_path / "tool-plan.json"
    path.write_text(
        json.dumps(
            {
                "stable_plan_key": "tool-cli-plan",
                "title": "Inspect tools",
                "objective": "Inspect tool eligibility without execution.",
                "status": "active",
                "planner_provenance": "human_authored",
                "repository_ref": "tool-cli-repository",
                "tasks": [
                    {
                        "task_key": "inspect",
                        "title": "Inspect tool eligibility",
                        "objective": "Produce an offline policy decision.",
                        "task_type": "analysis",
                        "status": "ready",
                        "affected_scopes": ["src/**"],
                        "forbidden_scopes": ["secrets/**"],
                        "requested_capabilities": ["read_repository", "run_tests"],
                        "provenance": "human_authored",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["plan", "import", "--file", str(path), "--project-root", str(repo), "--json"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    return payload["plan"]["plan_id"], payload["plan"]["tasks"][0]["task_id"]


def test_tool_list_show_registry_human_json_and_offline(monkeypatch: MonkeyPatch) -> None:
    class ForbiddenOllama:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("G1 tool inspection must not construct Ollama")

    monkeypatch.setattr(cli_module, "OllamaClient", ForbiddenOllama)

    def forbidden_operation(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("G1 registry inspection must not execute processes or access the network")

    monkeypatch.setattr(subprocess, "run", forbidden_operation)
    monkeypatch.setattr(socket, "create_connection", forbidden_operation)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_operation)
    runner = CliRunner()
    human = runner.invoke(app, ["tool", "list"])
    structured = runner.invoke(app, ["tool", "list", "--category", "file-read", "--json"])
    registry = runner.invoke(app, ["tool", "registry", "--json"])
    read_tool = builtin_registry().get_by_name_version("repository.read-file", "1.0.0")
    shown = runner.invoke(app, ["tool", "show", read_tool.tool_id])
    assert human.exit_code == structured.exit_code == registry.exit_code == shown.exit_code == 0
    assert "Execution available now: NO" in human.stdout
    assert all(item["category"] == "file-read" for item in json.loads(structured.stdout))
    assert json.loads(registry.stdout)["tool_count"] >= 28
    for label in ("effects", "required_capabilities", "scope_semantics", "approval_class"):
        assert label in shown.stdout


def test_task_tool_check_decision_list_show_forbidden_and_stale(
    tmp_repo: Path,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runner = CliRunner()
    plan_id, task_id = _import_plan(runner, tmp_repo, tmp_path)
    source_before = (tmp_repo / "app.py").read_bytes()

    class ForbiddenOllama:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("G1 task/tool checks must remain offline")

    monkeypatch.setattr(cli_module, "OllamaClient", ForbiddenOllama)

    def forbidden_operation(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("G1 policy inspection must not execute processes or access the network")

    monkeypatch.setattr(subprocess, "run", forbidden_operation)
    monkeypatch.setattr(socket, "create_connection", forbidden_operation)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_operation)
    registry = builtin_registry()
    inspect = registry.get_by_name_version("plan.inspect", "1.0.0")
    forbidden = registry.get_by_name_version("git.force-push", "1.0.0")
    common = [
        plan_id,
        "--task",
        task_id,
        "--repo",
        str(tmp_repo),
        "--project-root",
        str(tmp_repo),
    ]
    human = runner.invoke(app, ["plan", "tool-check", *common, "--tool", inspect.tool_id])
    structured = runner.invoke(
        app,
        ["plan", "tool-check", *common, "--tool", inspect.tool_id, "--json"],
    )
    denied = runner.invoke(
        app,
        ["plan", "tool-check", *common, "--tool", forbidden.tool_id, "--json"],
    )
    assert human.exit_code == structured.exit_code == denied.exit_code == 0
    assert "Execution available now" in human.stdout and "NO" in human.stdout
    decision = json.loads(structured.stdout)
    assert decision["structurally_eligible"] is True and decision["executable_now"] is False
    assert decision["granted_capabilities"] == []
    assert json.loads(denied.stdout)["decision"] == "permanently_forbidden"
    assert (tmp_repo / "app.py").read_bytes() == source_before

    listed = runner.invoke(
        app,
        ["plan", "tool-decision", "list", plan_id, "--task", task_id, "--project-root", str(tmp_repo), "--json"],
    )
    shown = runner.invoke(
        app,
        ["plan", "tool-decision", "show", plan_id, decision["decision_id"], "--project-root", str(tmp_repo), "--json"],
    )
    assert listed.exit_code == shown.exit_code == 0
    assert any(item["decision_id"] == decision["decision_id"] for item in json.loads(listed.stdout))
    assert json.loads(shown.stdout)["staleness"]["stale"] is False

    cli_module._planning_service(tmp_repo).transition_task(
        plan_id,
        task_id,
        TaskStatus.IN_PROGRESS,
        reason="Begin reviewed work",
        author=PlannerProvenance.HUMAN_AUTHORED,
    )
    stale = runner.invoke(
        app,
        ["plan", "tool-decision", "show", plan_id, decision["decision_id"], "--project-root", str(tmp_repo), "--json"],
    )
    assert stale.exit_code == 0
    assert json.loads(stale.stdout)["staleness"] == {"stale": True, "reasons": ["plan revision changed"]}


def test_module_entrypoint_exposes_tool_registry_and_policy_commands() -> None:
    root = Path(__file__).resolve().parents[2]
    for arguments, expected in (
        (["tool", "list", "--help"], "--category"),
        (["plan", "tool-check", "--help"], "--task"),
    ):
        result = subprocess.run(
            [sys.executable, "-m", "infinitecontex", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert expected in result.stdout


def test_stale_analysis_denies_repository_tool(tmp_repo: Path, tmp_path: Path) -> None:
    _profile(tmp_repo)
    plan_file = _plan_file(tmp_path, required_files=["app.py"])
    payload = json.loads(plan_file.read_text(encoding="utf-8"))
    payload["tasks"][0]["requested_capabilities"] = ["read_repository"]
    plan_file.write_text(json.dumps(payload), encoding="utf-8")
    runner = CliRunner()
    plan_id, task_id = _import(runner, tmp_repo, plan_file)
    fit = runner.invoke(
        app,
        [
            "plan",
            "context-fit",
            plan_id,
            "--task",
            task_id,
            "--model",
            "demo:latest",
            "--digest",
            "sha256:test",
            "--repo",
            str(tmp_repo),
            "--project-root",
            str(tmp_repo),
            "--json",
        ],
    )
    assert fit.exit_code == 0, fit.stdout
    (tmp_repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    read_tool = builtin_registry().get_by_name_version("repository.read-file", "1.0.0")
    stale = runner.invoke(
        app,
        [
            "plan",
            "tool-check",
            plan_id,
            "--task",
            task_id,
            "--tool",
            read_tool.tool_id,
            "--repo",
            str(tmp_repo),
            "--project-root",
            str(tmp_repo),
            "--json",
        ],
    )
    assert stale.exit_code == 0, stale.stdout
    decision = json.loads(stale.stdout)
    assert decision["decision"] == "denied_stale_analysis"
    assert "analysis_stale" in decision["reason_codes"]
    assert decision["executable_now"] is False
