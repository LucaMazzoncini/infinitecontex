from __future__ import annotations

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
        return ModelDetails(name=name, parameters="num_ctx 8192", model_info={"demo.context_length": 16384})


def test_budget_cli_human_and_json_use_only_persisted_profile(tmp_repo: Path, monkeypatch: MonkeyPatch) -> None:
    store = ModelProfileStore(build_layout(tmp_repo).model_profiles)
    ModelProfileService(
        FakeIdentityClient(),  # type: ignore[arg-type]
        store,
        clock=lambda: datetime(2026, 7, 14, tzinfo=UTC),
    ).create_or_reuse("demo:latest")

    class ForbiddenOllama:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("budget inspection must not construct an Ollama client")

    monkeypatch.setattr(cli_module, "OllamaClient", ForbiddenOllama)
    runner = CliRunner()
    human = runner.invoke(
        app,
        ["model", "budget", "show", "demo:latest", "--project-root", str(tmp_repo)],
    )
    assert human.exit_code == 0
    assert "Context Budget" in human.stdout
    assert "inspection_only" in human.stdout

    structured = runner.invoke(
        app,
        [
            "model",
            "budget",
            "estimate",
            "demo:latest",
            "--digest",
            "sha256:test",
            "--text",
            "hello world",
            "--project-root",
            str(tmp_repo),
            "--json",
        ],
    )
    assert structured.exit_code == 0
    assert '"decision": "pass_target"' in structured.stdout
    assert '"count_provenance": "heuristic"' in structured.stdout


def test_budget_cli_missing_profile_is_explicit(tmp_repo: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["model", "budget", "show", "missing:latest", "--project-root", str(tmp_repo)],
    )
    assert result.exit_code == 1
    assert "No persisted profile exists" in result.stdout
