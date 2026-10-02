import asyncio
from collections.abc import Sequence

from app.ai.interface import CategoryOption, TicketText, TriageModelError


class ScriptedModel:
    """A fake model: returns a canned answer, raises, or sleeps. Records what it was asked."""

    name = "fake-model"

    def __init__(
        self,
        answer: object = None,
        *,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.answer = answer
        self.error = error
        self.delay = delay
        self.calls: list[tuple[TicketText, list[CategoryOption]]] = []

    async def classify(self, ticket: TicketText, categories: Sequence[CategoryOption]) -> object:
        self.calls.append((ticket, list(categories)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.answer


def good_answer(category_id: int | None, **overrides: object) -> dict[str, object]:
    return {
        "category_id": category_id,
        "priority": "urgent",
        "sentiment": "negative",
        "suggested_reply": "Sorry about that. We are on it.",
    } | overrides


def model_unavailable() -> ScriptedModel:
    return ScriptedModel(error=TriageModelError("down"))


class NoTriage:
    """A runner that does nothing, to look at a ticket exactly as it is created."""

    async def run(self, ticket_id: object) -> None:
        return None
