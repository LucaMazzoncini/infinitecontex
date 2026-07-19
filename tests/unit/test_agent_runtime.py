import pytest

from infinitecontex.agent_runtime.models import FinalResponse, MutationCandidate, RuntimeBudgets, ToolRequest
from infinitecontex.agent_runtime.protocol import parse_response


def test_strict_response_protocol() -> None:
    tool = parse_response(
        '{"schema_version":1,"kind":"tool_request","action_type":"repo_read","arguments":{"path":"src/a.py"},"rationale":"Inspect"}'
    )
    assert isinstance(tool, ToolRequest)
    candidate = parse_response(
        '{"schema_version":1,"kind":"mutation_candidate","summary":"Change","operations":[],"suggested_validation_commands":[],"rationale":"Ready"}'
    )
    assert isinstance(candidate, MutationCandidate)
    final = parse_response(
        '{"schema_version":1,"kind":"final","status":"completed_without_mutation","summary":"Done","unresolved_items":[]}'
    )
    assert isinstance(final, FinalResponse)


@pytest.mark.parametrize(
    "value",
    [
        "prose {}",
        "{} {}",
        '{"schema_version":1,"kind":"unknown"}',
        '{"schema_version":1,"kind":"tool_request","action_type":"repo_read","arguments":{"path":"../x"},"rationale":"x"}',
        '{"schema_version":1,"kind":"final","status":"completed_without_mutation","summary":"x","unresolved_items":[],"unknown":1}',
    ],
)
def test_protocol_fails_closed(value: str) -> None:
    with pytest.raises(ValueError):
        parse_response(value)


def test_hard_runtime_budgets() -> None:
    assert RuntimeBudgets().maximum_model_calls == 24
    with pytest.raises(ValueError):
        RuntimeBudgets(maximum_model_calls=25)
