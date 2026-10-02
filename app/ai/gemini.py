"""Gemini adapter for AI triage. Docs: https://googleapis.github.io/python-genai/"""

from collections.abc import Sequence
from typing import Protocol

from google import genai
from google.genai import errors, types

from app.ai.budget import BudgetedTriageModel
from app.ai.interface import (
    CategoryOption,
    DisabledTriageModel,
    TicketText,
    TriageModel,
    TriageModelError,
)
from app.ai.prompts import build_prompt
from app.core.config import Settings

# Guides the model toward the shape that app.ai.output validates strictly afterwards.
RESPONSE_SCHEMA: dict[str, object] = {
    "type": "OBJECT",
    "required": ["category_id", "priority", "sentiment", "suggested_reply"],
    "properties": {
        "category_id": {"type": "INTEGER", "nullable": True},
        "priority": {"type": "STRING", "enum": ["low", "medium", "high", "urgent"]},
        "sentiment": {"type": "STRING", "enum": ["positive", "neutral", "negative"]},
        "suggested_reply": {"type": "STRING"},
    },
}


class _AsyncModels(Protocol):
    """The part of `client.aio.models` that the adapter uses (lets tests pass a fake)."""

    async def generate_content(
        self, *, model: str, contents: str, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse: ...


class GeminiTriageModel:
    def __init__(
        self,
        models: _AsyncModels,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        sdk_client: genai.Client | None = None,
    ) -> None:
        self._models = models
        # The SDK closes its shared HTTP connection when the client object that owns it is garbage
        # collected. Holding only `client.aio.models` let that happen at a random later moment,
        # and the first real request then failed with "Cannot send a request, as the client has
        # been closed".
        self._sdk_client = sdk_client
        self._max_output_tokens = max_output_tokens
        self.name = model
        self._timeout_ms = int(timeout_seconds * 1000)  # the SDK takes milliseconds

    async def aclose(self) -> None:
        """Close the SDK's connections (at app shutdown)."""
        if self._sdk_client is not None:
            await self._sdk_client.aio.aclose()
            self._sdk_client.close()

    async def classify(self, ticket: TicketText, categories: Sequence[CategoryOption]) -> object:
        prompt = build_prompt(ticket, categories)
        config = types.GenerateContentConfig(
            system_instruction=prompt.system,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            temperature=0.2,
            max_output_tokens=self._max_output_tokens,
            http_options=types.HttpOptions(timeout=self._timeout_ms),
        )
        try:
            response = await self._models.generate_content(
                model=self.name, contents=prompt.user, config=config
            )
        except errors.APIError as exc:
            # Only the status code: the provider's message is not ours to log or store.
            raise TriageModelError(
                f"gemini api error {exc.code}", status_code=exc.code, cause_type=type(exc).__name__
            ) from None
        candidates = response.candidates or []
        if candidates and candidates[0].finish_reason == types.FinishReason.MAX_TOKENS:
            raise TriageModelError("gemini answer was cut off by the output token limit")
        text = response.text
        if text is None or not text.strip():
            raise TriageModelError("gemini returned an empty answer")
        return text


def build_triage_model(settings: Settings) -> TriageModel:
    """The real model when a key is configured, otherwise the disabled one (keyword fallback)."""
    if settings.gemini_api_key is None:
        return DisabledTriageModel()
    client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
    gemini = GeminiTriageModel(
        client.aio.models,
        settings.gemini_model,
        settings.ai_timeout_seconds,
        settings.ai_max_output_tokens,
        sdk_client=client,
    )
    return BudgetedTriageModel(gemini, settings.ai_calls_per_minute)
