import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Category, Ticket, TicketEvent
from app.models.enums import (
    CategorySource,
    EventType,
    Priority,
    PrioritySource,
    TicketStatus,
)
from tests.support.factories import make_ticket
from tests.support.world import World

TICKETS = "/api/v1/tickets"


async def events(
    world: World, ticket: Ticket
) -> list[tuple[EventType, uuid.UUID | None, str | None, str | None]]:
    ticket_id = ticket.id
    world.session.expire_all()
    rows = await world.session.scalars(
        select(TicketEvent).where(TicketEvent.ticket_id == ticket_id).order_by(TicketEvent.id)
    )
    return [(e.event_type, e.actor_id, e.from_value, e.to_value) for e in rows]


async def reload(world: World, ticket: Ticket) -> Ticket:
    ticket_id = ticket.id
    world.session.expire_all()
    fresh = await world.session.get(Ticket, ticket_id)
    assert fresh is not None
    return fresh


# --- claim ----------------------------------------------------------------------------------


async def test_an_agent_claims_an_unassigned_ticket_without_changing_its_status(
    world: World,
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    response = await world.client.post(f"{TICKETS}/{ticket.id}/claim", headers=world.auth("agent"))

    assert response.status_code == 200
    assert response.json()["assignee_id"] == str(world.ids["agent"])
    assert response.json()["status"] == "open"
    assert await events(world, ticket) == [
        (EventType.ASSIGNED, world.ids["agent"], None, str(world.ids["agent"]))
    ]


async def test_an_admin_can_claim_too(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    response = await world.client.post(f"{TICKETS}/{ticket.id}/claim", headers=world.auth("admin"))

    assert response.json()["assignee_id"] == str(world.ids["admin"])


async def test_claiming_your_own_ticket_again_is_a_conflict(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])

    response = await world.client.post(f"{TICKETS}/{ticket.id}/claim", headers=world.auth("agent"))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_assigned"


async def test_an_admin_cannot_claim_a_ticket_assigned_to_someone_else(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])

    response = await world.client.post(f"{TICKETS}/{ticket.id}/claim", headers=world.auth("admin"))

    assert response.status_code == 409


async def test_another_agents_ticket_is_invisible_so_claiming_it_is_404(world: World) -> None:
    ticket = await make_ticket(
        world.session, world.ids["customer"], assignee_id=world.ids["agent2"]
    )

    response = await world.client.post(f"{TICKETS}/{ticket.id}/claim", headers=world.auth("agent"))

    assert response.status_code == 404


async def test_two_agents_claiming_at_the_same_time_one_wins_and_one_gets_409(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    first, second = await asyncio.gather(
        world.client.post(f"{TICKETS}/{ticket.id}/claim", headers=world.auth("agent")),
        world.client.post(f"{TICKETS}/{ticket.id}/claim", headers=world.auth("agent2")),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    loser = first if first.status_code == 409 else second
    assert loser.json()["error"]["code"] == "already_assigned"
    assert len([e for e in await events(world, ticket) if e[0] == EventType.ASSIGNED]) == 1


async def test_a_closed_ticket_cannot_be_claimed(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], status=TicketStatus.CLOSED)

    response = await world.client.post(f"{TICKETS}/{ticket.id}/claim", headers=world.auth("agent"))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ticket_closed"


async def test_customers_cannot_claim(world: World) -> None:
    own = await make_ticket(world.session, world.ids["customer"])
    other = await make_ticket(world.session, world.ids["customer2"])

    on_own = await world.client.post(f"{TICKETS}/{own.id}/claim", headers=world.auth("customer"))
    on_other = await world.client.post(
        f"{TICKETS}/{other.id}/claim", headers=world.auth("customer")
    )

    assert (on_own.status_code, on_other.status_code) == (403, 404)


# --- release --------------------------------------------------------------------------------


async def test_the_assignee_releases_a_ticket_and_status_stays(world: World) -> None:
    ticket = await make_ticket(
        world.session,
        world.ids["customer"],
        assignee_id=world.ids["agent"],
        status=TicketStatus.IN_PROGRESS,
    )

    response = await world.client.post(
        f"{TICKETS}/{ticket.id}/release", headers=world.auth("agent")
    )

    assert response.status_code == 200
    assert response.json()["assignee_id"] is None
    assert response.json()["status"] == "in_progress"
    assert await events(world, ticket) == [
        (EventType.RELEASED, world.ids["agent"], str(world.ids["agent"]), None)
    ]


async def test_an_agent_cannot_release_an_unassigned_ticket_they_do_not_hold(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    response = await world.client.post(
        f"{TICKETS}/{ticket.id}/release", headers=world.auth("agent")
    )

    assert response.status_code == 403


async def test_an_admin_can_release_any_ticket_and_releasing_an_unassigned_one_is_a_no_op(
    world: World,
) -> None:
    held = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent2"])
    free = await make_ticket(world.session, world.ids["customer"])

    released = await world.client.post(f"{TICKETS}/{held.id}/release", headers=world.auth("admin"))
    noop = await world.client.post(f"{TICKETS}/{free.id}/release", headers=world.auth("admin"))

    assert released.json()["assignee_id"] is None
    assert noop.status_code == 200
    assert await events(world, free) == []


async def test_a_closed_ticket_cannot_be_released_and_customers_cannot_release(
    world: World,
) -> None:
    closed = await make_ticket(
        world.session,
        world.ids["customer"],
        assignee_id=world.ids["agent"],
        status=TicketStatus.CLOSED,
    )
    own = await make_ticket(world.session, world.ids["customer"])

    on_closed = await world.client.post(
        f"{TICKETS}/{closed.id}/release", headers=world.auth("agent")
    )
    by_customer = await world.client.post(
        f"{TICKETS}/{own.id}/release", headers=world.auth("customer")
    )

    assert on_closed.json()["error"]["code"] == "ticket_closed"
    assert by_customer.status_code == 403


# --- assign ---------------------------------------------------------------------------------


async def test_an_admin_assigns_and_reassigns_a_ticket(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])

    response = await world.client.put(
        f"{TICKETS}/{ticket.id}/assignee",
        json={"assignee_id": str(world.ids["agent2"])},
        headers=world.auth("admin"),
    )

    assert response.status_code == 200
    assert response.json()["assignee_id"] == str(world.ids["agent2"])
    assert await events(world, ticket) == [
        (EventType.ASSIGNED, world.ids["admin"], str(world.ids["agent"]), str(world.ids["agent2"]))
    ]


async def test_assigning_the_current_assignee_changes_nothing(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])

    response = await world.client.put(
        f"{TICKETS}/{ticket.id}/assignee",
        json={"assignee_id": str(world.ids["agent"])},
        headers=world.auth("admin"),
    )

    assert response.status_code == 200
    assert await events(world, ticket) == []


@pytest.mark.parametrize("target", ["customer2", "nobody"])
async def test_the_assignee_must_be_an_agent_or_admin(world: World, target: str) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])
    assignee = str(uuid.uuid4()) if target == "nobody" else str(world.ids[target])

    response = await world.client.put(
        f"{TICKETS}/{ticket.id}/assignee",
        json={"assignee_id": assignee},
        headers=world.auth("admin"),
    )

    assert response.status_code == 422


@pytest.mark.parametrize("body", [{}, {"assignee_id": "not-a-uuid"}, {"assignee_id": None}])
async def test_assignment_validates_the_body(world: World, body: dict[str, object]) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    response = await world.client.put(
        f"{TICKETS}/{ticket.id}/assignee", json=body, headers=world.auth("admin")
    )

    assert response.status_code == 422


async def test_a_closed_ticket_cannot_be_assigned(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], status=TicketStatus.CLOSED)

    response = await world.client.put(
        f"{TICKETS}/{ticket.id}/assignee",
        json={"assignee_id": str(world.ids["agent"])},
        headers=world.auth("admin"),
    )

    assert response.status_code == 409


async def test_only_admins_assign(world: World) -> None:
    visible = await make_ticket(world.session, world.ids["customer"])
    invisible = await make_ticket(
        world.session, world.ids["customer"], assignee_id=world.ids["agent2"]
    )
    own = await make_ticket(world.session, world.ids["customer"])
    body = {"assignee_id": str(world.ids["agent"])}

    agent_visible = await world.client.put(
        f"{TICKETS}/{visible.id}/assignee", json=body, headers=world.auth("agent")
    )
    agent_invisible = await world.client.put(
        f"{TICKETS}/{invisible.id}/assignee", json=body, headers=world.auth("agent")
    )
    customer = await world.client.put(
        f"{TICKETS}/{own.id}/assignee", json=body, headers=world.auth("customer")
    )

    assert (agent_visible.status_code, agent_invisible.status_code, customer.status_code) == (
        403,
        404,
        403,
    )


# --- overrides ------------------------------------------------------------------------------


async def test_the_assignee_overrides_priority_and_sla_is_recalculated_from_created_at(
    world: World,
) -> None:
    created = datetime.now(UTC) - timedelta(hours=3)
    ticket = await make_ticket(
        world.session, world.ids["customer"], assignee_id=world.ids["agent"], created_at=created
    )

    response = await world.client.patch(
        f"{TICKETS}/{ticket.id}", json={"priority": "urgent"}, headers=world.auth("agent")
    )

    fresh = await reload(world, ticket)
    assert response.status_code == 200
    assert fresh.priority == Priority.URGENT
    assert fresh.priority_source == PrioritySource.HUMAN
    assert fresh.first_response_due_at == created + timedelta(hours=1)
    assert fresh.resolution_due_at == created + timedelta(hours=4)
    assert response.json()["sla_breached"] is True
    assert await events(world, ticket) == [
        (EventType.PRIORITY_CHANGED, world.ids["agent"], "medium", "urgent")
    ]


async def test_the_assignee_sets_an_active_category(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    billing = await world.session.scalar(select(Category).where(Category.name == "Billing"))
    assert billing is not None
    billing_id = billing.id

    response = await world.client.patch(
        f"{TICKETS}/{ticket.id}", json={"category_id": billing_id}, headers=world.auth("agent")
    )

    fresh = await reload(world, ticket)
    assert response.json()["category_id"] == billing_id
    assert fresh.category_source == CategorySource.HUMAN
    assert await events(world, ticket) == [
        (EventType.CATEGORY_CHANGED, world.ids["agent"], None, str(billing_id))
    ]


async def test_both_fields_at_once_write_two_events(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    technical = await world.session.scalar(
        select(Category).where(Category.name == "Technical Issue")
    )
    assert technical is not None

    response = await world.client.patch(
        f"{TICKETS}/{ticket.id}",
        json={"category_id": technical.id, "priority": "high"},
        headers=world.auth("agent"),
    )

    assert response.status_code == 200
    assert {e[0] for e in await events(world, ticket)} == {
        EventType.CATEGORY_CHANGED,
        EventType.PRIORITY_CHANGED,
    }


async def test_an_inactive_or_unknown_category_is_rejected(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    billing = await world.session.scalar(select(Category).where(Category.name == "Billing"))
    assert billing is not None
    billing.is_active = False
    await world.session.commit()

    inactive = await world.client.patch(
        f"{TICKETS}/{ticket.id}", json={"category_id": billing.id}, headers=world.auth("agent")
    )
    unknown = await world.client.patch(
        f"{TICKETS}/{ticket.id}", json={"category_id": 999999}, headers=world.auth("agent")
    )

    assert (inactive.status_code, unknown.status_code) == (422, 422)


async def test_setting_the_same_priority_is_a_no_op(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])

    response = await world.client.patch(
        f"{TICKETS}/{ticket.id}", json={"priority": "medium"}, headers=world.auth("agent")
    )

    fresh = await reload(world, ticket)
    assert response.status_code == 200
    assert fresh.priority_source == PrioritySource.DEFAULT
    assert await events(world, ticket) == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"category_id": None},
        {"priority": "critical"},
        {"title": "new"},
        {"priority": "high", "status": "closed"},
    ],
)
async def test_override_body_is_validated(world: World, body: dict[str, object]) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])

    response = await world.client.patch(
        f"{TICKETS}/{ticket.id}", json=body, headers=world.auth("agent")
    )

    assert response.status_code == 422


async def test_override_permissions(world: World) -> None:
    unassigned = await make_ticket(world.session, world.ids["customer"])
    others = await make_ticket(
        world.session, world.ids["customer"], assignee_id=world.ids["agent2"]
    )
    own = await make_ticket(world.session, world.ids["customer"])
    body = {"priority": "high"}

    not_assignee = await world.client.patch(
        f"{TICKETS}/{unassigned.id}", json=body, headers=world.auth("agent")
    )
    invisible = await world.client.patch(
        f"{TICKETS}/{others.id}", json=body, headers=world.auth("agent")
    )
    customer = await world.client.patch(
        f"{TICKETS}/{own.id}", json=body, headers=world.auth("customer")
    )
    admin = await world.client.patch(
        f"{TICKETS}/{others.id}", json=body, headers=world.auth("admin")
    )

    assert (
        not_assignee.status_code,
        invisible.status_code,
        customer.status_code,
        admin.status_code,
    ) == (403, 404, 403, 200)


async def test_a_closed_ticket_cannot_be_overridden(world: World) -> None:
    ticket = await make_ticket(
        world.session,
        world.ids["customer"],
        assignee_id=world.ids["agent"],
        status=TicketStatus.CLOSED,
    )

    response = await world.client.patch(
        f"{TICKETS}/{ticket.id}", json={"priority": "high"}, headers=world.auth("agent")
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ticket_closed"
