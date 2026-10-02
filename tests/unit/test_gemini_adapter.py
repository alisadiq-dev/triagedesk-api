import json
from typing import Any

import pytest
from google.genai import errors, types
from pydantic import SecretStr

from app.ai.gemini import GeminiTriageModel, build_triage_model
from app.ai.interface import CategoryOption, DisabledTriageModel, TicketText, TriageModelError
from app.ai.output import parse_output
from app.core.config import Settings

TICKET = TicketText("Cannot log in", "The reset email never arrives")
CATEGORIES = [CategoryOption(1, "Account Access", "Login problems"), CategoryOption(2, "Billing")]
ANSWER = {
    "category_id": 1,
    "priority": "high",
    "sentiment": "negative",
    "suggested_reply": "Sorry, we are looking into it.",
}


class FakeResponse:
    def __init__(self, text: str | None) -> None:
        self.text = text


class FakeModels:
    """Stands in for client.aio.models: records the call and returns or raises."""

    def __init__(
        self, text: str | None = json.dumps(ANSWER), error: Exception | None = None
    ) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate_content(
        self, *, model: str, contents: str, config: types.GenerateContentConfig
    ) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error is not None:
            raise self.error
        return FakeResponse(self.text)


def make_model(models: FakeModels, timeout: float = 15.0) -> GeminiTriageModel:
    return GeminiTriageModel(models, model="gemini-test", timeout_seconds=timeout)


async def test_returns_the_model_text_which_passes_output_validation() -> None:
    raw = await make_model(FakeModels()).classify(TICKET, CATEGORIES)

    assert parse_output(raw).priority.value == "high"


async def test_sends_the_versioned_prompt_as_system_instruction_and_data_as_contents() -> None:
    models = FakeModels()

    await make_model(models).classify(TICKET, CATEGORIES)

    call = models.calls[0]
    assert call["model"] == "gemini-test"
    assert "untrusted customer data" in str(call["config"].system_instruction)
    assert "Cannot log in" in call["contents"]
    assert "1: Account Access - Login problems" in call["contents"]
    assert "Cannot log in" not in str(call["config"].system_instruction)


async def test_asks_for_json_with_a_schema_and_a_millisecond_http_timeout() -> None:
    models = FakeModels()

    await make_model(models, timeout=7.5).classify(TICKET, CATEGORIES)

    config = models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema is not None
    assert config.http_options.timeout == 7500


async def test_api_errors_become_triage_model_errors_without_leaking_the_message() -> None:
    error = errors.APIError(429, {"error": {"message": "quota for key AIza-secret-123"}})

    with pytest.raises(TriageModelError) as raised:
        await make_model(FakeModels(error=error)).classify(TICKET, CATEGORIES)

    assert "429" in str(raised.value)
    assert "AIza-secret-123" not in str(raised.value)


@pytest.mark.parametrize("text", [None, "", "   "])
async def test_an_empty_answer_is_a_triage_model_error(text: str | None) -> None:
    models = FakeModels(text=text)

    with pytest.raises(TriageModelError):
        await make_model(models).classify(TICKET, CATEGORIES)


def test_the_model_name_is_stored_for_traceability() -> None:
    assert make_model(FakeModels()).name == "gemini-test"


def test_without_a_key_the_disabled_model_is_used() -> None:
    settings = Settings(_env_file=None, database_url=SecretStr("postgresql+asyncpg://x/y"))

    assert isinstance(build_triage_model(settings), DisabledTriageModel)


def test_with_a_key_the_gemini_model_uses_the_configured_name() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr("postgresql+asyncpg://x/y"),
        gemini_api_key=SecretStr("fake-key"),
        gemini_model="gemini-other",
    )

    model = build_triage_model(settings)

    assert isinstance(model, GeminiTriageModel)
    assert model.name == "gemini-other"
