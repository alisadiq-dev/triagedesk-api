import json

import pytest

from app.ai.output import InvalidTriageOutputError, TriageOutput, parse_output
from app.models.enums import Priority, Sentiment

VALID = {
    "category_id": 2,
    "priority": "high",
    "sentiment": "negative",
    "suggested_reply": "Sorry about that. We are looking into it.",
}


def test_a_valid_dict_is_parsed() -> None:
    assert parse_output(VALID) == TriageOutput(
        category_id=2,
        priority=Priority.HIGH,
        sentiment=Sentiment.NEGATIVE,
        suggested_reply="Sorry about that. We are looking into it.",
    )


def test_a_json_string_is_parsed_even_inside_a_code_fence() -> None:
    fenced = "```json\n" + json.dumps(VALID) + "\n```"

    assert parse_output(json.dumps(VALID)).priority == Priority.HIGH
    assert parse_output(fenced).priority == Priority.HIGH


def test_a_null_category_is_allowed() -> None:
    assert parse_output({**VALID, "category_id": None}).category_id is None


def test_the_reply_is_trimmed() -> None:
    assert parse_output({**VALID, "suggested_reply": "  hello  "}).suggested_reply == "hello"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        42,
        [],
        "not json at all",
        '{"priority": "high"}',  # missing keys
        {**VALID, "priority": "critical"},
        {**VALID, "sentiment": "angry"},
        {**VALID, "category_id": "2"},  # strict: no string-to-int coercion
        {**VALID, "category_id": 2.5},
        {**VALID, "category_id": True},
        {**VALID, "suggested_reply": ""},
        {**VALID, "suggested_reply": "x" * 2001},
        {**VALID, "suggested_reply": 5},
        {**VALID, "role": "admin"},  # extra keys are rejected: output is data, not commands
        {**VALID, "assignee_id": "5f0c1e2a-8c0e-4a53-9a41-2f6f1f0b7d11"},
        {**VALID, "status": "closed"},
    ],
)
def test_anything_else_is_invalid(raw: object) -> None:
    with pytest.raises(InvalidTriageOutputError):
        parse_output(raw)


def test_an_invalid_output_never_echoes_the_content() -> None:
    with pytest.raises(InvalidTriageOutputError) as caught:
        parse_output({**VALID, "priority": "SECRET-LEAK"})

    assert "SECRET-LEAK" not in str(caught.value)
