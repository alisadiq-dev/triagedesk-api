import asyncio
import itertools
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import select

from app.models import Ticket, TicketEvent
from app.models.enums import EventType, TicketStatus
from app.repositories.events import EventRepository
from tests.support.factories import make_ticket
from tests.support.world import World

S = TicketStatus
TICKETS = "/api/v1/tickets"

VALID = [
    (S.OPEN, S.IN_PROGRESS),
    (S.IN_PROGRESS, S.WAITING_ON_CUSTOMER),
    (S.IN_PROGRESS, S.RESOLVED),
    (S.WAITING_ON_CUSTOMER, S.IN_PROGRESS),
    (S.RESOLVED, S.CLOSED),
    (S.RESOLVED, S.IN_PROGRESS),
]
# The API accepts only these four targets (open is never a target). Closed tickets are final.
TARGETS = [S.IN_PROGRESS, S.WAITING_ON_CUSTOMER, S.RESOLVED, S.CLOSED]
INVALID = [
    (current, target)
    for current, target in itertools.product(
        [S.OPEN, S.IN_PROGRESS, S.WAITING_ON_CUSTOMER, S.RESOLVED], TARGETS
    )
    if (current, target) not in VALID
]


async def change(world: World, ticket: Ticket, status: str, who: str = "agent") -> httpx2.Response:
    return await world.client.post(
        f"{TICKETS}/{ticket.id}/status", json={"status": status}, headers=world.auth(who)
    )


async def row(world: World, ticket_id: object) -> Ticket:
    world.session.expire_all()
    fresh = await world.session.get(Ticket, ticket_id)
    assert fresh is not None
    return fresh


async def history(
    world: World, ticket_id: object
) -> list[tuple[EventType, str | None, str | None]]:
    world.session.expire_all()
    rows = await world.session.scalars(
        select(TicketEvent).where(TicketEvent.ticket_id == ticket_id).order_by(TicketEvent.id)
    )
    return [(e.event_type, e.from_value, e.to_value) for e in rows]


@pytest.mark.parametrize(("current", "target"), VALID)
async def test_every_valid_transition_succeeds_and_writes_one_status_event(
    world: World, current: TicketStatus, target: TicketStatus
) -> None:
    ticket = await make_ticket(
        world.session, world.ids["customer"], status=current, assignee_id=world.ids["agent"]
    )
    ticket_id = ticket.id

    response = await change(world, ticket, target.value)

    assert response.status_code == 200
    assert response.json()["status"] == target.value
    assert (await row(world, ticket_id)).status == target
    status_events = [e for e in await history(world, ticket_id) if e[0] == EventType.STATUS_CHANGED]
    assert status_events == [(EventType.STATUS_CHANGED, current.value, target.value)]


@pytest.mark.parametrize(("current", "target"), INVALID)
async def test_every_invalid_transition_is_a_409_and_changes_nothing(
    world: World, current: TicketStatus, target: TicketStatus
) -> None:
    ticket = await make_ticket(
        world.session, world.ids["customer"], status=current, assignee_id=world.ids["agent"]
    )
    ticket_id = ticket.id

    response = await change(world, ticket, target.value)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_transition"
    assert current.value in response.json()["error"]["message"]
    assert (await row(world, ticket_id)).status == current
    assert await history(world, ticket_id) == []


@pytest.mark.parametrize("target", TARGETS)
async def test_a_closed_ticket_is_final(world: World, target: TicketStatus) -> None:
    ticket = await make_ticket(
        world.session, world.ids["customer"], status=S.CLOSED, assignee_id=world.ids["agent"]
    )

    response = await change(world, ticket, target.value)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ticket_closed"


@pytest.mark.parametrize(
    "body", [{"status": "open"}, {"status": "done"}, {}, {"status": "closed", "x": 1}]
)
async def test_the_status_body_is_validated(world: World, body: dict[str, object]) -> None:
    ticket = await make_ticket(
        world.session, world.ids["customer"], status=S.RESOLVED, assignee_id=world.ids["agent"]
    )

    response = await world.client.post(
        f"{TICKETS}/{ticket.id}/status", json=body, headers=world.auth("agent")
    )

    assert response.status_code == 422


async def test_resolving_sets_resolved_at_and_closing_keeps_it(world: World) -> None:
    ticket = await make_ticket(
        world.session, world.ids["customer"], status=S.IN_PROGRESS, assignee_id=world.ids["agent"]
    )
    ticket_id = ticket.id

    await change(world, ticket, "resolved")
    resolved_at = (await row(world, ticket_id)).resolved_at
    await change(world, ticket, "closed")

    assert resolved_at is not None
    assert (await row(world, ticket_id)).resolved_at == resolved_at


async def test_reopening_clears_resolved_at_and_keeps_the_old_value_in_the_audit_log(
    world: World,
) -> None:
    ticket = await make_ticket(
        world.session, world.ids["customer"], status=S.IN_PROGRESS, assignee_id=world.ids["agent"]
    )
    ticket_id = ticket.id
    await change(world, ticket, "resolved")
    resolved_at = (await row(world, ticket_id)).resolved_at
    assert resolved_at is not None

    response = await change(world, ticket, "in_progress")

    assert response.status_code == 200
    assert response.json()["resolved_at"] is None
    assert (await row(world, ticket_id)).resolved_at is None
    events = await history(world, ticket_id)
    assert (EventType.RESOLVED_AT_CLEARED, resolved_at.isoformat(), None) in events


async def test_a_reopened_ticket_with_a_passed_resolution_deadline_shows_as_breached(
    world: World,
) -> None:
    ticket = await make_ticket(
        world.session,
        world.ids["customer"],
        status=S.RESOLVED,
        assignee_id=world.ids["agent"],
        first_responded_at=datetime.now(UTC) - timedelta(hours=5),
        resolution_due_at=datetime.now(UTC) - timedelta(hours=1),
    )

    response = await change(world, ticket, "in_progress")

    assert response.json()["sla_breached"] is True


async def test_the_full_lifecycle_writes_an_ordered_audit_trail(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    ticket_id = ticket.id

    for step in ("in_progress", "waiting_on_customer", "in_progress", "resolved", "closed"):
        assert (await change(world, ticket, step)).status_code == 200

    assert [
        (e[1], e[2]) for e in await history(world, ticket_id) if e[0] == EventType.STATUS_CHANGED
    ] == [
        ("open", "in_progress"),
        ("in_progress", "waiting_on_customer"),
        ("waiting_on_customer", "in_progress"),
        ("in_progress", "resolved"),
        ("resolved", "closed"),
    ]


async def test_the_status_change_and_its_event_are_one_transaction(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    ticket_id = ticket.id

    def failing_add(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(EventRepository, "add", failing_add)
    with pytest.raises(RuntimeError):
        await change(world, ticket, "in_progress")

    assert (await row(world, ticket_id)).status == S.OPEN


# --- permissions ----------------------------------------------------------------------------


async def test_only_the_assignee_or_an_admin_can_change_status(world: World) -> None:
    held = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    free = await make_ticket(world.session, world.ids["customer"])

    assignee = await change(world, held, "in_progress", "agent")
    other_agent = await change(world, held, "in_progress", "agent2")
    unclaimed = await change(world, free, "in_progress", "agent")
    admin_on_free = await change(world, free, "in_progress", "admin")

    assert [r.status_code for r in (assignee, other_agent, unclaimed, admin_on_free)] == [
        200,
        404,
        403,
        200,
    ]


async def test_customers_can_never_change_status(world: World) -> None:
    own = await make_ticket(world.session, world.ids["customer"], status=S.RESOLVED)
    other = await make_ticket(world.session, world.ids["customer2"], status=S.RESOLVED)

    on_own = await change(world, own, "closed", "customer")
    on_other = await change(world, other, "closed", "customer")

    assert (on_own.status_code, on_other.status_code) == (403, 404)


async def test_status_changes_require_authentication(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    response = await world.client.post(f"{TICKETS}/{ticket.id}/status", json={"status": "closed"})

    assert response.status_code == 401


async def test_two_simultaneous_identical_changes_make_one_transition_and_one_event(
    world: World,
) -> None:
    ticket = await make_ticket(
        world.session, world.ids["customer"], status=S.IN_PROGRESS, assignee_id=world.ids["agent"]
    )
    ticket_id = ticket.id

    first, second = await asyncio.gather(
        change(world, ticket, "resolved", "agent"), change(world, ticket, "resolved", "admin")
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert (await row(world, ticket_id)).status == S.RESOLVED
    assert [e for e in await history(world, ticket_id) if e[0] == EventType.STATUS_CHANGED] == [
        (EventType.STATUS_CHANGED, "in_progress", "resolved")
    ]
