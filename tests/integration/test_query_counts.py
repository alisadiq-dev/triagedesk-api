"""CONSTRAINTS.md #8: list endpoints run a constant number of queries, whatever the row count."""

import pytest
from sqlalchemy import event

from app.api.deps import ensure_database
from app.models import TicketComment, TicketEvent
from app.models.enums import EventType, Priority, Role
from tests.support.factories import make_ticket
from tests.support.world import World

TICKETS = "/api/v1/tickets"
Param = str | int | bool


class QueryCounter:
    def __init__(self, world: World) -> None:
        self._engine = ensure_database(world.app).engine.sync_engine
        self.count = 0

    def _on_execute(self, *_: object) -> None:
        self.count += 1

    def __enter__(self) -> "QueryCounter":
        event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *_: object) -> None:
        event.remove(self._engine, "before_cursor_execute", self._on_execute)


async def queries_for(world: World, who: str, url: str, **params: Param) -> int:
    with QueryCounter(world) as counter:
        response = await world.client.get(
            url, params={"page_size": 100, **params}, headers=world.auth(who)
        )
    assert response.status_code == 200, response.text
    return counter.count


async def add_tickets(world: World, how_many: int) -> None:
    for index in range(how_many):
        await make_ticket(
            world.session,
            world.ids["customer"],
            assignee_id=world.ids["agent"] if index % 2 else None,
            priority=list(Priority)[index % 4],
        )


SCENARIOS: list[tuple[str, dict[str, Param]]] = [
    ("customer", {}),
    ("customer", {"status": "open", "q": "printer"}),
    ("agent", {}),
    ("agent", {"status": "open", "sort": "first_response_due_at"}),
    ("agent", {"q": "printer", "sla_breached": False, "unassigned": True}),
    ("admin", {}),
    ("admin", {"priority": "high", "category_id": 1, "assignee_id": "{agent}"}),
    ("admin", {"sla_breached": True, "created_after": "2020-01-01T00:00:00+00:00"}),
]


@pytest.mark.parametrize(("who", "params"), SCENARIOS)
async def test_ticket_list_query_count_does_not_grow_with_rows(
    world: World, who: str, params: dict[str, Param]
) -> None:
    params = {
        key: str(world.ids["agent"]) if value == "{agent}" else value
        for key, value in params.items()
    }
    await add_tickets(world, 1)
    await queries_for(world, who, TICKETS)  # warm-up: creates the pool and the profile row
    with_one = await queries_for(world, who, TICKETS, **params)

    await add_tickets(world, 29)
    with_thirty = await queries_for(world, who, TICKETS, **params)

    assert with_one >= 2  # the counter really sees the profile, count and page queries
    assert with_thirty == with_one


async def test_comment_list_query_count_does_not_grow_with_rows(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    url = f"{TICKETS}/{ticket.id}/comments"

    async def add_comments(how_many: int) -> None:
        for index in range(how_many):
            author, role = (
                (world.ids["agent"], Role.AGENT)
                if index % 2
                else (world.ids["customer"], Role.CUSTOMER)
            )
            world.session.add(
                TicketComment(ticket_id=ticket.id, author_id=author, author_role=role, body="hi")
            )
        await world.session.commit()

    await add_comments(1)
    await queries_for(world, "agent", url)
    one = await queries_for(world, "agent", url)
    await add_comments(29)
    thirty = await queries_for(world, "agent", url)
    customer_thirty = await queries_for(world, "customer", url)
    await queries_for(world, "customer", url)

    assert thirty == one
    assert customer_thirty == thirty


async def test_event_list_query_count_does_not_grow_with_rows(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    url = f"{TICKETS}/{ticket.id}/events"

    async def add_events(how_many: int) -> None:
        for _ in range(how_many):
            world.session.add(
                TicketEvent(
                    ticket_id=ticket.id,
                    actor_id=world.ids["agent"],
                    event_type=EventType.STATUS_CHANGED,
                    from_value="open",
                    to_value="in_progress",
                )
            )
        await world.session.commit()

    await add_events(1)
    await queries_for(world, "agent", url)
    one = await queries_for(world, "agent", url)
    await add_events(29)
    thirty = await queries_for(world, "agent", url)

    assert thirty == one
