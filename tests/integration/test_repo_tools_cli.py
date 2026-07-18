from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

from pytest import MonkeyPatch
from test_task_context_cli import _profile
from typer.testing import CliRunner

import infinitecontex.cli as cli_module
from infinitecontex.cli import app


def _plain_repo(tmp_path: Path) -> Path:
    root = tmp_path / "plain-repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("class ContextAdmissionGate:\n    pass\n# TODO literal\n", encoding="utf-8")
    (root / "README.md").write_text("context demo\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
    return root


def _forbid_external_operations(monkeypatch: MonkeyPatch) -> None:
    class ForbiddenOllama:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("G2 repository tools must not construct Ollama")

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("G2 repository tools must not use subprocess or network")

    monkeypatch.setattr(cli_module, "OllamaClient", ForbiddenOllama)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)


def test_repo_files_read_search_sensitive_records_human_json_offline(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    root = _plain_repo(tmp_path)
    source_before = (root / "src" / "app.py").read_bytes()
    _forbid_external_operations(monkeypatch)
    runner = CliRunner()
    files_human = runner.invoke(app, ["repo", "files", "--repo", str(root), "--project-root", str(root)])
    files_json = runner.invoke(
        app,
        ["repo", "files", "--glob", "src/**/*.py", "--repo", str(root), "--project-root", str(root), "--json"],
    )
    read_human = runner.invoke(
        app,
        ["repo", "read", "src/app.py", "--max-lines", "2", "--repo", str(root), "--project-root", str(root)],
    )
    read_json = runner.invoke(
        app,
        [
            "repo",
            "read",
            "src/app.py",
            "--start-line",
            "2",
            "--end-line",
            "3",
            "--repo",
            str(root),
            "--project-root",
            str(root),
            "--json",
        ],
    )
    search_human = runner.invoke(
        app,
        ["repo", "search", "ContextAdmissionGate", "--repo", str(root), "--project-root", str(root)],
    )
    search_json = runner.invoke(
        app,
        ["repo", "search", "context", "--ignore-case", "--repo", str(root), "--project-root", str(root), "--json"],
    )
    no_match = runner.invoke(
        app,
        ["repo", "search", "never-present", "--repo", str(root), "--project-root", str(root), "--json"],
    )
    sensitive = runner.invoke(
        app,
        ["repo", "read", ".env", "--repo", str(root), "--project-root", str(root), "--json"],
    )
    assert all(
        value.exit_code == 0
        for value in (files_human, files_json, read_human, read_json, search_human, search_json, no_match, sensitive)
    )
    assert "src/app.py" in files_human.stdout and "ContextAdmissionGate" in read_human.stdout
    assert json.loads(files_json.stdout)["result"]["files"][0]["path"] == "src/app.py"
    assert json.loads(read_json.stdout)["result"]["returned_range"] == [2, 3]
    assert "src/app.py:1:7" in search_human.stdout
    assert json.loads(search_json.stdout)["result"]["matches"]
    assert json.loads(no_match.stdout)["result"]["status"] == "no_matches"
    assert json.loads(sensitive.stdout)["result"]["status"] == "sensitive_path"
    assert (root / "src" / "app.py").read_bytes() == source_before
    listed = runner.invoke(app, ["tool", "execution", "list", "--project-root", str(root), "--json"])
    records = json.loads(listed.stdout)
    assert listed.exit_code == 0 and len(records) >= 7
    shown = runner.invoke(
        app,
        ["tool", "execution", "show", records[0]["execution_id"], "--project-root", str(root), "--json"],
    )
    assert shown.exit_code == 0 and "content" not in json.loads(shown.stdout)


def test_task_bound_read_exact_context_scope_and_no_persistent_grant(tmp_path: Path) -> None:
    root = _plain_repo(tmp_path)
    _profile(root)
    plan_file = tmp_path / "g2-plan.json"
    plan_file.write_text(
        json.dumps(
            {
                "stable_plan_key": "g2-task-read",
                "title": "Task-bound repository read",
                "objective": "Read exact declared source through G2.",
                "status": "active",
                "planner_provenance": "human_authored",
                "repository_ref": "plain-repository",
                "tasks": [
                    {
                        "task_key": "read",
                        "title": "Read app source",
                        "objective": "Inspect the exact app source.",
                        "task_type": "analysis",
                        "status": "ready",
                        "affected_scopes": ["src/app.py"],
                        "forbidden_scopes": ["secrets/**"],
                        "requested_capabilities": ["read_repository"],
                        "context_requirements": {"required_files": ["src/app.py"]},
                        "provenance": "human_authored",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    imported = runner.invoke(app, ["plan", "import", "--file", str(plan_file), "--project-root", str(root), "--json"])
    payload = json.loads(imported.stdout)
    plan_id = payload["plan"]["plan_id"]
    task_id = payload["plan"]["tasks"][0]["task_id"]
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
            str(root),
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert fit.exit_code == 0, fit.stdout
    read = runner.invoke(
        app,
        [
            "plan",
            "tool-read",
            plan_id,
            "--task",
            task_id,
            "--path",
            "src/app.py",
            "--repo",
            str(root),
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert read.exit_code == 0, read.stdout
    result = json.loads(read.stdout)
    assert result["result"]["status"] in {"success", "partial_success"}
    assert result["invocation"]["task_id"] == task_id
    records = list((root / ".infctx" / "tool-executions").glob("*.json"))
    assert len(records) == 1 and not list((root / ".infctx").rglob("read-grant-*.json"))


def test_module_entrypoint_exposes_repo_tools() -> None:
    root = Path(__file__).resolve().parents[2]
    for arguments, expected in (
        (["repo", "files", "--help"], "--glob"),
        (["repo", "read", "--help"], "--start-line"),
        (["repo", "search", "--help"], "--ignore-case"),
        (["plan", "tool-read", "--help"], "--task"),
    ):
        result = subprocess.run(
            [sys.executable, "-m", "infinitecontex", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0 and expected in result.stdout
