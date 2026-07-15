from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pytest import MonkeyPatch
from test_task_context_cli import _import, _plan_file, _profile
from typer.testing import CliRunner

import infinitecontex.cli as cli_module
from infinitecontex.cli import app


def _split(
    runner: CliRunner,
    repo: Path,
    plan_id: str,
    task_id: str,
    *,
    json_output: bool = False,
):
    arguments = [
        "plan",
        "split",
        plan_id,
        "--task",
        task_id,
        "--model",
        "demo:latest",
        "--digest",
        "sha256:test",
        "--repo",
        str(repo),
        "--project-root",
        str(repo),
    ]
    if json_output:
        arguments.append("--json")
    return runner.invoke(app, arguments)


def test_split_proposal_human_json_list_show_approve_and_revision(
    tmp_repo: Path,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    _profile(tmp_repo)
    plan_file = _plan_file(tmp_path, required_files=["app.py", "README.md"])
    plan_payload = json.loads(plan_file.read_text(encoding="utf-8"))
    plan_payload["tasks"][0].update(
        {
            "acceptance_criteria": [
                {
                    "criterion_id": "criterion-reviewed",
                    "description": "Every split child remains reviewable.",
                    "verification_method": "Inspect proposal mappings.",
                    "required_evidence_type": "human_approval",
                    "provenance": "human_authored",
                }
            ],
            "required_evidence": [
                {
                    "evidence_id": "evidence-proposal",
                    "evidence_type": "human_approval",
                    "description": "Explicit proposal decision.",
                }
            ],
            "affected_scopes": ["app.py", "README.md"],
            "requested_capabilities": ["read_repository"],
        }
    )
    plan_file.write_text(json.dumps(plan_payload), encoding="utf-8")
    runner = CliRunner()
    plan_id, task_id = _import(runner, tmp_repo, plan_file)
    original_sources = {path.name: path.read_bytes() for path in (tmp_repo / "app.py", tmp_repo / "README.md")}

    class ForbiddenOllama:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("split proposal CLI must remain offline")

    monkeypatch.setattr(cli_module, "OllamaClient", ForbiddenOllama)
    human = _split(runner, tmp_repo, plan_id, task_id)
    structured = _split(runner, tmp_repo, plan_id, task_id, json_output=True)
    assert human.exit_code == 0, human.stdout
    for label in (
        "source_plan",
        "why_split",
        "selected_rule",
        "context_fit_before",
        "leaf_context_fit",
        "child_scopes",
        "criterion_evidence_mappings",
        "dependency_rewrites",
        "requested_capabilities",
        "contract_coverage",
        "every_required_leaf_fits",
        "result_preview",
    ):
        assert label in human.stdout
    proposal = json.loads(structured.stdout)
    proposal_id = proposal["proposal_id"]
    assert structured.exit_code == 0
    assert proposal["source_context_fit"]["passing"] is True
    assert proposal["direct_child_count"] == 2
    assert proposal["every_leaf_fits"] is True
    assert "proposed_plan" in proposal
    assert "criterion-reviewed" in human.stdout
    assert "evidence-proposal" in human.stdout
    assert "read_repository" in human.stdout

    listed = runner.invoke(
        app,
        [
            "plan",
            "split-proposal",
            "list",
            plan_id,
            "--project-root",
            str(tmp_repo),
            "--json",
        ],
    )
    shown = runner.invoke(
        app,
        [
            "plan",
            "split-proposal",
            "show",
            plan_id,
            proposal_id,
            "--project-root",
            str(tmp_repo),
        ],
    )
    assert listed.exit_code == 0 and json.loads(listed.stdout)[0]["decision"] == "pending"
    assert shown.exit_code == 0 and proposal_id in shown.stdout

    approved = runner.invoke(
        app,
        [
            "plan",
            "approve-split",
            plan_id,
            proposal_id,
            "--actor",
            "User",
            "--reason",
            "Reviewed",
            "--acknowledge-warnings",
            "--repo",
            str(tmp_repo),
            "--project-root",
            str(tmp_repo),
            "--json",
        ],
    )
    assert approved.exit_code == 0, approved.stdout
    approval_payload = json.loads(approved.stdout)
    approval = approval_payload["approval"]
    assert approval["decision"] == "approved"
    assert approval_payload["resulting_revision"]["revision"] == 2

    decisions = runner.invoke(
        app,
        [
            "plan",
            "approval",
            "list",
            plan_id,
            "--project-root",
            str(tmp_repo),
            "--json",
        ],
    )
    decision = runner.invoke(
        app,
        [
            "plan",
            "approval",
            "show",
            plan_id,
            approval["approval_id"],
            "--project-root",
            str(tmp_repo),
            "--json",
        ],
    )
    history = runner.invoke(
        app,
        ["plan", "history", plan_id, "--project-root", str(tmp_repo), "--json"],
    )
    historical = runner.invoke(
        app,
        [
            "plan",
            "show",
            plan_id,
            "--revision",
            "1",
            "--project-root",
            str(tmp_repo),
            "--json",
        ],
    )
    assert decisions.exit_code == decision.exit_code == 0
    assert json.loads(decisions.stdout)[0]["application_status"] == "applied"
    assert json.loads(decision.stdout)["proposal_fingerprint"] == proposal["semantic_fingerprint"]
    assert len(json.loads(history.stdout)) == 2
    assert json.loads(historical.stdout)["plan"]["tasks"][0]["metadata"].get("split_barrier") is None
    assert {path.name: path.read_bytes() for path in (tmp_repo / "app.py", tmp_repo / "README.md")} == original_sources

    duplicate = runner.invoke(
        app,
        [
            "plan",
            "approve-split",
            plan_id,
            proposal_id,
            "--actor",
            "User",
            "--acknowledge-warnings",
            "--repo",
            str(tmp_repo),
            "--project-root",
            str(tmp_repo),
        ],
    )
    assert duplicate.exit_code == 1 and "duplicate decisions" in duplicate.stdout


def test_reject_and_stale_proposal_never_change_plan(tmp_repo: Path, tmp_path: Path) -> None:
    _profile(tmp_repo)
    runner = CliRunner()
    rejected_plan, rejected_task = _import(
        runner,
        tmp_repo,
        _plan_file(tmp_path, required_files=["app.py", "README.md"]),
    )
    rejected_proposal = json.loads(_split(runner, tmp_repo, rejected_plan, rejected_task, json_output=True).stdout)
    rejected = runner.invoke(
        app,
        [
            "plan",
            "reject-split",
            rejected_plan,
            rejected_proposal["proposal_id"],
            "--actor",
            "User",
            "--reason",
            "Incorrect boundaries",
            "--project-root",
            str(tmp_repo),
            "--json",
        ],
    )
    assert rejected.exit_code == 0 and json.loads(rejected.stdout)["decision"] == "rejected"
    current = runner.invoke(
        app,
        ["plan", "show", rejected_plan, "--project-root", str(tmp_repo), "--json"],
    )
    assert json.loads(current.stdout)["plan"]["current_revision"] == 1

    stale_plan, stale_task = _import(
        runner,
        tmp_repo,
        _plan_file(tmp_path, required_files=["app.py", "README.md"]),
    )
    stale_proposal = json.loads(_split(runner, tmp_repo, stale_plan, stale_task, json_output=True).stdout)
    (tmp_repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    stale = runner.invoke(
        app,
        [
            "plan",
            "approve-split",
            stale_plan,
            stale_proposal["proposal_id"],
            "--actor",
            "User",
            "--acknowledge-warnings",
            "--repo",
            str(tmp_repo),
            "--project-root",
            str(tmp_repo),
        ],
    )
    assert stale.exit_code == 1 and "Repository changed" in stale.stdout
    stale_current = runner.invoke(
        app,
        ["plan", "show", stale_plan, "--project-root", str(tmp_repo), "--json"],
    )
    assert json.loads(stale_current.stdout)["plan"]["current_revision"] == 1


def test_module_entrypoint_exposes_split_workflow() -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "infinitecontex", "plan", "split", "--help"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--task" in result.stdout and "--digest" in result.stdout
