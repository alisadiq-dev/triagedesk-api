"""The demo script is exercised against the real app, so it cannot drift from the API."""

import pytest
from sqlalchemy import func, select

from app.models import Ticket, TicketComment
from scripts.demo import DemoError, run_demo
from tests.support.world import World

TOKENS = {"customer": "customer-token", "agent": "agent-token", "admin": "admin-token"}


class Output:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, text: str) -> None:
        self.lines.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


async def test_the_whole_demo_runs_against_the_app(world: World) -> None:
    out = Output()

    await run_demo(world.client, TOKENS, out, pause=0, wait_seconds=10)

    tickets = await world.session.scalar(select(func.count()).select_from(Ticket))
    comments = await world.session.scalar(select(func.count()).select_from(TicketComment))
    assert tickets == 1
    assert comments == 3  # a public reply, an internal note, and the customer's answer
    for phrase in ("customer sees", "staff see", "invalid transition", "audit trail", "closed"):
        assert phrase.lower() in out.text.lower(), phrase


async def test_the_demo_never_prints_tokens(world: World) -> None:
    out = Output()

    await run_demo(world.client, TOKENS, out, pause=0, wait_seconds=10)

    assert "-token" not in out.text
    assert "Bearer" not in out.text


async def test_the_rate_limit_step_hits_429_without_creating_tickets(world: World) -> None:
    out = Output()

    await run_demo(world.client, TOKENS, out, pause=0, wait_seconds=10, show_rate_limit=True)

    tickets = await world.session.scalar(select(func.count()).select_from(Ticket))
    assert tickets == 1  # only the demo ticket; the burst used invalid bodies
    assert "429" in out.text


async def test_an_unexpected_status_stops_the_demo_with_a_clear_error(world: World) -> None:
    broken = {**TOKENS, "agent": "customer-token"}  # the "agent" cannot claim: it is a customer

    with pytest.raises(DemoError, match="claim"):
        await run_demo(world.client, broken, Output(), pause=0, wait_seconds=10)
