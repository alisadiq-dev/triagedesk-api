"""Validation of the model's answer. Anything that does not match exactly is rejected."""

import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StrictInt, StringConstraints, ValidationError

from app.models.enums import Priority, Sentiment

SuggestedReply = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class InvalidTriageOutputError(Exception):
    """The model answered with an unexpected shape. The message never contains the answer."""


class TriageOutput(BaseModel):
    """Only these four fields can come out of the model. Extra keys are an error, not ignored."""

    model_config = ConfigDict(extra="forbid", strict=True)

    category_id: StrictInt | None
    priority: Priority
    sentiment: Sentiment
    suggested_reply: SuggestedReply


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def parse_output(raw: object) -> TriageOutput:
    """Validate in strict JSON mode: enums from their string values, no number or bool coercion."""
    if isinstance(raw, str):
        text = _strip_code_fence(raw)
    elif isinstance(raw, dict):
        try:
            text = json.dumps(raw)
        except (TypeError, ValueError) as exc:
            raise InvalidTriageOutputError("model output is not JSON-serializable") from exc
    else:
        raise InvalidTriageOutputError("model output is not a JSON object")
    try:
        return TriageOutput.model_validate_json(text)
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
        detail = ", ".join(fields) if fields else "not a JSON object"
        raise InvalidTriageOutputError(f"model output does not match the schema: {detail}") from exc
