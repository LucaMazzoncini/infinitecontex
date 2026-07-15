from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

import infinitecontex.cli as cli_module
from infinitecontex.cli import app

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "plans" / "minimal-plan.json"


def test_plan_validation_human_json_does_not_persist_or_construct_ollama(
    tmp_repo: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_module, "OllamaClient", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))
    runner = CliRunner()
    human = runner.invoke(app, ["--project-root", str(tmp_repo), "plan", "validate", "--file", str(EXAMPLE)])
    structured = runner.invoke(
        app, ["--project-root", str(tmp_repo), "plan", "validate", "--file", str(EXAMPLE), "--json"]
    )
    assert human.exit_code == 0 and "valid" in human.stdout
    assert structured.exit_code == 0
    payload = json.loads(structured.stdout)
    assert payload["valid"] is True and payload["task_count"] == 2
    assert not (tmp_repo / ".infctx" / "plans").exists()


def test_plan_import_list_show_history_graph_ready_and_explicit_revision(tmp_repo: Path) -> None:
    runner = CliRunner()
    imported = runner.invoke(
        app,
        ["plan", "import", "--file", str(EXAMPLE), "--project-root", str(tmp_repo), "--json"],
    )
    assert imported.exit_code == 0, imported.stdout
    payload = json.loads(imported.stdout)
    plan_id = payload["plan"]["plan_id"]
    assert payload["persisted"] is True
    repeated = runner.invoke(
        app,
        ["plan", "import", "--file", str(EXAMPLE), "--project-root", str(tmp_repo), "--json"],
    )
    assert json.loads(repeated.stdout)["persisted"] is False
    listed = runner.invoke(app, ["plan", "list", "--project-root", str(tmp_repo), "--json"])
    shown = runner.invoke(app, ["plan", "show", plan_id, "--project-root", str(tmp_repo), "--json"])
    historical = runner.invoke(
        app, ["plan", "show", plan_id, "--revision", "1", "--project-root", str(tmp_repo), "--json"]
    )
    history = runner.invoke(app, ["plan", "history", plan_id, "--project-root", str(tmp_repo), "--json"])
    graph = runner.invoke(app, ["plan", "graph", plan_id, "--project-root", str(tmp_repo), "--json"])
    ready = runner.invoke(app, ["plan", "ready", plan_id, "--project-root", str(tmp_repo), "--json"])
    for result in (listed, shown, historical, history, graph, ready):
        assert result.exit_code == 0, result.stdout
    assert plan_id in listed.stdout and plan_id in shown.stdout
    assert len(json.loads(history.stdout)) == 1
    assert [item["task_key"] for item in json.loads(graph.stdout)] == ["design", "validate"]
    assert [item["task_key"] for item in json.loads(ready.stdout)] == ["design"]


def test_cyclic_plan_validation_and_import_fail_without_partial_persistence(tmp_repo: Path, tmp_path: Path) -> None:
    cyclic = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    cyclic["tasks"][0]["dependency_keys"] = ["validate"]
    path = tmp_path / "cyclic.json"
    path.write_text(json.dumps(cyclic), encoding="utf-8")
    runner = CliRunner()
    validated = runner.invoke(app, ["plan", "validate", "--file", str(path), "--json"])
    imported = runner.invoke(app, ["plan", "import", "--file", str(path), "--project-root", str(tmp_repo), "--json"])
    assert validated.exit_code == 2
    assert json.loads(validated.stdout)["detected_cycles"]
    assert imported.exit_code == 1
    assert "Plan is invalid" in imported.stdout
    assert not (tmp_repo / ".infctx" / "plans").exists()


def test_plan_cli_missing_plan_and_oversized_file(tmp_repo: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    missing = runner.invoke(app, ["plan", "show", "plan-000000000000000000000000", "--project-root", str(tmp_repo)])
    assert missing.exit_code == 1 and "was not found" in missing.stdout
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    result = runner.invoke(app, ["plan", "validate", "--file", str(oversized)])
    assert result.exit_code == 1 and "safe limit" in result.stdout
