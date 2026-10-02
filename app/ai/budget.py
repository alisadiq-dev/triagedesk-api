"""A global cap on paid model calls, so a flood of tickets cannot run up an unbounded bill."""

import time
from collections.abc import Callable, Sequence

from app.ai.interface import CategoryOption, TicketText, TriageModel, TriageModelError
from app.core.rate_limit import RateLimiter


class BudgetedTriageModel:
    """Wraps a model. Over budget, the call fails and the triage takes the keyword fallback."""

    def __init__(
        self,
        inner: TriageModel,
        calls_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self._limiter = RateLimiter(calls_per_minute, 60.0, clock=clock, max_keys=1)

    async def classify(self, ticket: TicketText, categories: Sequence[CategoryOption]) -> object:
        if self._limiter.check("model") is not None:
            raise TriageModelError("model call budget used up for this minute")
        return await self.inner.classify(ticket, categories)

    async def aclose(self) -> None:
        close = getattr(self.inner, "aclose", None)
        if close is not None:
            await close()
