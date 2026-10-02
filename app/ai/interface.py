"""The boundary between the app and any LLM. Everything the model returns is untrusted data."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TicketText:
    title: str
    description: str


@dataclass(frozen=True)
class CategoryOption:
    id: int
    name: str
    description: str | None = None


class TriageModelError(Exception):
    """The model could not be used (not configured, network error, refused, ...)."""


class TriageModel(Protocol):
    name: str  # stored with each result for traceability

    async def classify(self, ticket: TicketText, categories: Sequence[CategoryOption]) -> object:
        """Return the model's raw answer (a dict or a JSON string). Never trust it: validate."""
        ...


class DisabledTriageModel:
    """Used when no real model is configured: every triage takes the keyword fallback path."""

    name = "disabled"

    async def classify(self, ticket: TicketText, categories: Sequence[CategoryOption]) -> object:
        raise TriageModelError("no AI model is configured")
