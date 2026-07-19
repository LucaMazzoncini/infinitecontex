"""Strict single-object model protocol; never extracts JSON from prose."""

import orjson
from pydantic import ValidationError

from infinitecontex.agent_runtime.models import Checkpoint, FinalResponse, MutationCandidate, ToolRequest

Response = ToolRequest | MutationCandidate | Checkpoint | FinalResponse


def parse_response(text: str) -> Response:
    if not text or text != text.strip() or not text.startswith("{") or not text.endswith("}"):
        raise ValueError("response must be exactly one JSON object without surrounding prose")
    try:
        value = orjson.loads(text)
    except orjson.JSONDecodeError as exc:
        raise ValueError("response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("response must be an object")
    try:
        kind = value.get("kind")
        if kind == "tool_request":
            response: Response = ToolRequest.model_validate(value)
        elif kind == "mutation_candidate":
            response = MutationCandidate.model_validate(value)
        elif kind == "checkpoint":
            response = Checkpoint.model_validate(value)
        elif kind == "final":
            response = FinalResponse.model_validate(value)
        else:
            raise ValueError("unknown response kind")
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    if isinstance(response, ToolRequest):
        for item in response.arguments.values():
            if isinstance(item, str) and (
                ".." in item.replace("\\", "/").split("/") or item.startswith(("/", "\\")) or ":" in item[:3]
            ):
                raise ValueError("absolute paths and traversal are forbidden")
    return response
