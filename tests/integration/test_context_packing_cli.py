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
        return ModelDetails(name=name, parameters="num_ctx 8192", model_info={"demo.context_length": 16384})


def _profile(tmp_repo: Path) -> None:
    ModelProfileService(
        FakeIdentityClient(),  # type: ignore[arg-type]
        ModelProfileStore(build_layout(tmp_repo).model_profiles),
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
    ).create_or_reuse("demo:latest")


def _candidate_file(tmp_path: Path) -> Path:
    path = tmp_path / "candidates.json"
    path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "candidate_id": "task",
                        "category": "current_task",
                        "label": "Task",
                        "content": "Keep packing deterministic.",
                        "mandatory": True,
                        "retention_priority": 100,
                    },
                    {
                        "candidate_id": "source",
                        "category": "source_code_excerpt",
                        "label": "Source",
                        "content": "def pack(): pass",
                        "direct_request_match": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_pack_human_json_persist_list_show_and_no_ollama(
    tmp_repo: Path, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _profile(tmp_repo)
    preserved = tmp_repo / ".infctx" / "keep.txt"
    preserved.write_text("user-data", encoding="utf-8")
    candidates = _candidate_file(tmp_path)

    class ForbiddenOllama:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("context packing must not construct Ollama")

    monkeypatch.setattr(cli_module, "OllamaClient", ForbiddenOllama)
    runner = CliRunner()
    human = runner.invoke(
        app,
        [
            "context",
            "pack",
            "--model",
            "demo:latest",
            "--digest",
            "sha256:test",
            "--candidate-file",
            str(candidates),
            "--project-root",
            str(tmp_repo),
        ],
    )
    assert human.exit_code == 0, human.stdout
    assert "Context Packing Manifest" in human.stdout
    assert "inspection_only" in human.stdout
    assert preserved.read_text(encoding="utf-8") == "user-data"

    json_result = runner.invoke(
        app,
        [
            "context",
            "pack",
            "--model",
            "demo:latest",
            "--digest",
            "sha256:test",
            "--candidate-file",
            str(candidates),
            "--project-root",
            str(tmp_repo),
            "--no-persist",
            "--json",
        ],
    )
    assert json_result.exit_code == 0, json_result.stdout
    payload = json.loads(json_result.stdout)
    assert payload["decision"] in {"packed", "packed_with_warning"}
    assert len(payload["included"]) == 2
    assert "content" not in payload["included"][0]["candidate"]

    listed = runner.invoke(
        app,
        ["context", "manifest", "list", "--project-root", str(tmp_repo), "--json"],
    )
    assert listed.exit_code == 0
    manifests = json.loads(listed.stdout)
    assert len(manifests) == 1
    manifest_id = manifests[0]["manifest_id"]
    shown = runner.invoke(
        app,
        ["context", "manifest", "show", manifest_id, "--project-root", str(tmp_repo)],
    )
    assert shown.exit_code == 0
    assert manifest_id in shown.stdout


def test_pack_invalid_oversize_missing_profile_and_digest(
    tmp_repo: Path, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    runner = CliRunner()
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"candidates":"not-a-list"}', encoding="utf-8")
    result = runner.invoke(
        app,
        ["context", "pack", "--model", "missing", "--candidate-file", str(invalid)],
    )
    assert result.exit_code == 1
    assert "candidates" in result.stdout

    monkeypatch.setattr(cli_module, "CONTEXT_CANDIDATE_FILE_LIMIT_BYTES", 4)
    oversized = tmp_path / "oversized.json"
    oversized.write_text("[]   ", encoding="utf-8")
    result = runner.invoke(
        app,
        ["context", "pack", "--model", "missing", "--candidate-file", str(oversized)],
    )
    assert result.exit_code == 1
    assert "--allow-large-file" in result.stdout

    _profile(tmp_repo)
    candidates = _candidate_file(tmp_path)
    monkeypatch.setattr(cli_module, "CONTEXT_CANDIDATE_FILE_LIMIT_BYTES", 8 * 1024 * 1024)
    mismatch = runner.invoke(
        app,
        [
            "context",
            "pack",
            "--model",
            "demo:latest",
            "--digest",
            "sha256:other",
            "--candidate-file",
            str(candidates),
            "--project-root",
            str(tmp_repo),
        ],
    )
    assert mismatch.exit_code == 1
    assert "not digest" in mismatch.stdout

    missing = runner.invoke(
        app,
        [
            "context",
            "pack",
            "--model",
            "missing:latest",
            "--candidate-file",
            str(candidates),
            "--project-root",
            str(tmp_repo),
        ],
    )
    assert missing.exit_code == 1
    assert "No persisted profile" in missing.stdout
