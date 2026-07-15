from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

import infinitecontex.cli as cli_module
from infinitecontex.cli import app
from infinitecontex.llm.models import InstalledModel, ModelDetails
from infinitecontex.model_profiles.service import ModelProfileService
from infinitecontex.model_profiles.store import ModelProfileStore
from infinitecontex.storage.layout import build_layout


class FakeIdentityClient:
    def list_models(self) -> list[InstalledModel]:
        return [InstalledModel(name="demo:latest", digest="sha256:test")]

    def show_model(self, name: str) -> ModelDetails:
        return ModelDetails(name=name, parameters="num_ctx 8192", model_info={"demo.context_length": 999999})


def _profile(root: Path) -> None:
    ModelProfileService(
        FakeIdentityClient(),  # type: ignore[arg-type]
        ModelProfileStore(build_layout(root).model_profiles),
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
    ).create_or_reuse("demo:latest")


def _plan_file(tmp_path: Path, *, required_files: list[str], required_symbols: list[str] | None = None) -> Path:
    path = tmp_path / ("plan-" + str(len(list(tmp_path.glob("plan-*.json")))) + ".json")
    path.write_text(
        json.dumps(
            {
                "stable_plan_key": path.stem,
                "title": "Resolve explicit context",
                "objective": "Inspect declared paths and symbols offline.",
                "planner_provenance": "human_authored",
                "repository_ref": "cli-test-repository",
                "tasks": [
                    {
                        "task_key": "inspect",
                        "title": "Inspect declaration",
                        "objective": "Resolve explicit context only.",
                        "description": "No repository retrieval or execution.",
                        "task_type": "analysis",
                        "status": "ready",
                        "provenance": "human_authored",
                        "context_requirements": {
                            "required_files": required_files,
                            "required_symbols": required_symbols or [],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _import(runner: CliRunner, repo: Path, plan_file: Path) -> tuple[str, str]:
    result = runner.invoke(
        app,
        ["plan", "import", "--file", str(plan_file), "--project-root", str(repo), "--json"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    return payload["plan"]["plan_id"], payload["plan"]["tasks"][0]["task_id"]


def test_resolve_fit_analysis_list_show_human_json_and_no_ollama(
    tmp_repo: Path, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _profile(tmp_repo)
    plan_file = _plan_file(tmp_path, required_files=["app.py"], required_symbols=["app.py::run"])
    runner = CliRunner()
    plan_id, task_id = _import(runner, tmp_repo, plan_file)

    class ForbiddenOllama:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("task context inspection must remain offline")

    monkeypatch.setattr(cli_module, "OllamaClient", ForbiddenOllama)
    common = [plan_id, "--repo", str(tmp_repo), "--project-root", str(tmp_repo)]
    human = runner.invoke(app, ["plan", "resolve", *common])
    structured = runner.invoke(app, ["plan", "resolve", *common, "--json"])
    assert human.exit_code == 0 and "resolved" in human.stdout
    resolution = json.loads(structured.stdout)
    assert resolution["reports"][0]["path_resolutions"][0]["outcome"] == "resolved"
    assert resolution["reports"][0]["symbol_resolutions"][0]["outcome"] == "resolved_with_file_hint"

    fit = runner.invoke(
        app,
        [
            "plan",
            "context-fit",
            *common,
            "--task",
            task_id,
            "--model",
            "demo:latest",
            "--digest",
            "sha256:test",
            "--json",
        ],
    )
    assert fit.exit_code == 0, fit.stdout
    analysis = json.loads(fit.stdout)[0]
    assert analysis["passing"] is True
    assert analysis["decision"] in {"fits_target", "fits_with_warning", "fits_hard_limit"}
    assert analysis["maximum_recommended_input_tokens"] < 8192
    assert "content" not in analysis["included_candidates"][0]

    listed = runner.invoke(
        app,
        ["plan", "context-analysis", "list", plan_id, "--project-root", str(tmp_repo), "--json"],
    )
    shown = runner.invoke(
        app,
        [
            "plan",
            "context-analysis",
            "show",
            plan_id,
            analysis["analysis_id"],
            "--repo",
            str(tmp_repo),
            "--project-root",
            str(tmp_repo),
            "--json",
        ],
    )
    assert listed.exit_code == 0 and len(json.loads(listed.stdout)) == 1
    shown_payload = json.loads(shown.stdout)
    assert shown.exit_code == 0 and shown_payload["staleness"]["stale"] is False, shown_payload


def test_unresolved_required_and_unsupported_symbol_fail_visibly(tmp_repo: Path, tmp_path: Path) -> None:
    _profile(tmp_repo)
    runner = CliRunner()
    missing_id, _ = _import(runner, tmp_repo, _plan_file(tmp_path, required_files=["missing.py"]))
    missing = runner.invoke(
        app,
        [
            "plan",
            "context-fit",
            missing_id,
            "--repo",
            str(tmp_repo),
            "--project-root",
            str(tmp_repo),
            "--model",
            "demo:latest",
            "--digest",
            "sha256:test",
            "--json",
        ],
    )
    assert missing.exit_code == 2
    assert json.loads(missing.stdout)[0]["decision"] == "required_reference_unresolved"

    (tmp_repo / "Player.cs").write_text("class Player {}", encoding="utf-8")
    unsupported_id, _ = _import(
        runner,
        tmp_repo,
        _plan_file(tmp_path, required_files=["Player.cs"], required_symbols=["Player.cs::Player"]),
    )
    unsupported = runner.invoke(
        app,
        [
            "plan",
            "context-fit",
            unsupported_id,
            "--repo",
            str(tmp_repo),
            "--project-root",
            str(tmp_repo),
            "--model",
            "demo:latest",
            "--digest",
            "sha256:test",
            "--json",
        ],
    )
    assert unsupported.exit_code == 2
    assert json.loads(unsupported.stdout)[0]["decision"] == "unsupported_required_symbol"


def test_oversized_context_and_missing_repository_fail_visibly(tmp_repo: Path, tmp_path: Path) -> None:
    _profile(tmp_repo)
    plan_file = _plan_file(tmp_path, required_files=["app.py"])
    payload = json.loads(plan_file.read_text(encoding="utf-8"))
    payload["tasks"][0]["context_requirements"]["maximum_context_tokens"] = 1
    plan_file.write_text(json.dumps(payload), encoding="utf-8")
    runner = CliRunner()
    plan_id, _ = _import(runner, tmp_repo, plan_file)

    oversized = runner.invoke(
        app,
        [
            "plan",
            "context-fit",
            plan_id,
            "--repo",
            str(tmp_repo),
            "--project-root",
            str(tmp_repo),
            "--model",
            "demo:latest",
            "--digest",
            "sha256:test",
        ],
    )
    assert oversized.exit_code == 2
    assert "Task Context Analyses" in oversized.stdout
    oversized_json = runner.invoke(
        app,
        [
            "plan",
            "context-fit",
            plan_id,
            "--repo",
            str(tmp_repo),
            "--project-root",
            str(tmp_repo),
            "--model",
            "demo:latest",
            "--digest",
            "sha256:test",
            "--no-persist",
            "--json",
        ],
    )
    oversized_payload = json.loads(oversized_json.stdout)[0]
    assert oversized_json.exit_code == 2
    assert oversized_payload["decision"] == "split_required"
    assert oversized_payload["token_deficit"] > 0

    missing_repository = runner.invoke(
        app,
        [
            "plan",
            "resolve",
            plan_id,
            "--repo",
            str(tmp_path / "does-not-exist"),
            "--project-root",
            str(tmp_repo),
            "--json",
        ],
    )
    assert missing_repository.exit_code == 1
    assert "repository" in missing_repository.stdout.lower()


def test_module_entrypoint_exposes_offline_task_context_commands(tmp_repo: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["plan", "resolve", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.stdout and "--task" in result.stdout
