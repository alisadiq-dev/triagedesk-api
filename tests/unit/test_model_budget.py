import pytest
from pydantic import SecretStr

from app.ai.budget import BudgetedTriageModel
from app.ai.gemini import GeminiTriageModel, build_triage_model
from app.ai.interface import TicketText, TriageModelError
from app.core.config import Settings
from tests.support.ai import ScriptedModel, good_answer

TICKET = TicketText("t", "d")


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_calls_inside_the_budget_reach_the_model() -> None:
    inner = ScriptedModel(good_answer(None))
    model = BudgetedTriageModel(inner, calls_per_minute=2)

    await model.classify(TICKET, [])
    await model.classify(TICKET, [])

    assert len(inner.calls) == 2


async def test_calls_over_the_budget_are_refused_without_calling_the_model() -> None:
    inner = ScriptedModel(good_answer(None))
    model = BudgetedTriageModel(inner, calls_per_minute=2)
    await model.classify(TICKET, [])
    await model.classify(TICKET, [])

    with pytest.raises(TriageModelError, match="budget"):
        await model.classify(TICKET, [])

    assert len(inner.calls) == 2


async def test_the_budget_recovers_after_the_minute() -> None:
    clock = Clock()
    inner = ScriptedModel(good_answer(None))
    model = BudgetedTriageModel(inner, calls_per_minute=1, clock=clock)
    await model.classify(TICKET, [])

    clock.now += 61

    await model.classify(TICKET, [])
    assert len(inner.calls) == 2


def test_the_name_of_the_wrapped_model_is_kept_for_traceability() -> None:
    assert BudgetedTriageModel(ScriptedModel(), calls_per_minute=1).name == "fake-model"


def test_the_real_model_is_wrapped_in_the_budget() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr("postgresql+asyncpg://x/y"),
        gemini_api_key=SecretStr("fake-key"),
        ai_calls_per_minute=5,
    )

    model = build_triage_model(settings)

    assert isinstance(model, BudgetedTriageModel)
    assert isinstance(model.inner, GeminiTriageModel)
