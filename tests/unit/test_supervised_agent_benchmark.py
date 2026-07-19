from pathlib import Path
from runpy import run_path

generate = run_path(str(Path("benchmarks/supervised_agent/generate_fixture.py")))["generate"]


def test_fixture_is_deterministic_and_hidden_evaluator_is_separate(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate(first)
    generate(second)
    visible = sorted(path.relative_to(first).as_posix() for path in (first / "visible").rglob("*") if path.is_file())
    assert 15 <= len(visible) <= 25
    lines = sum(
        len(path.read_text(encoding="utf-8").splitlines()) for path in (first / "visible").rglob("*") if path.is_file()
    )
    assert 2_000 <= lines <= 4_000
    assert (first / "evaluator" / "test_retry_contract.py").is_file()
    assert all(not item.startswith("evaluator/") for item in visible)
    for relative in sorted(path.relative_to(first) for path in first.rglob("*") if path.is_file()):
        assert (first / relative).read_bytes() == (second / relative).read_bytes()
