import uuid

import pytest

from app.models import Ticket
from app.models.enums import TicketStatus
from tests.support.factories import make_ticket
from tests.support.world import World

EVENT_KEYS = {"id", "event_type", "actor_id", "from_value", "to_value", "created_at"}


def url(ticket: Ticket) -> str:
    return f"/api/v1/tickets/{ticket.id}/events"


async def worked_ticket(world: World) -> Ticket:
    """A ticket created through the API, then claimed, overridden and moved through statuses."""
    created = await world.client.post(
        "/api/v1/tickets", json={"title": "t", "description": "d"}, headers=world.auth("customer")
    )
    ticket_id = created.json()["id"]
    await world.client.post(f"/api/v1/tickets/{ticket_id}/claim", headers=world.auth("agent"))
    await world.client.patch(
        f"/api/v1/tickets/{ticket_id}", json={"priority": "high"}, headers=world.auth("agent")
    )
    await world.client.post(
        f"/api/v1/tickets/{ticket_id}/status",
        json={"status": "in_progress"},
        headers=world.auth("agent"),
    )
    ticket = await world.session.get(Ticket, uuid.UUID(ticket_id))
    assert ticket is not None
    return ticket


async def test_staff_see_the_full_audit_trail_oldest_first(world: World) -> None:
    ticket = await worked_ticket(world)

    response = await world.client.get(url(ticket), headers=world.auth("agent"))

    body = response.json()
    assert response.status_code == 200
    assert [(e["event_type"], e["from_value"], e["to_value"]) for e in body["items"]] == [
        ("ticket_created", None, "open"),
        ("assigned", None, str(world.ids["agent"])),
        ("priority_changed", "medium", "high"),
        ("status_changed", "open", "in_progress"),
    ]
    assert all(set(e) == EVENT_KEYS for e in body["items"])
    assert body["items"][0]["actor_id"] == str(world.ids["customer"])
    assert body["total"] == 4


async def test_events_are_paginated_with_the_shared_shape(world: World) -> None:
    ticket = await worked_ticket(world)

    response = await world.client.get(
        url(ticket), params={"page": 2, "page_size": 3}, headers=world.auth("admin")
    )

    body = response.json()
    assert (body["page"], body["page_size"], body["total"]) == (2, 3, 4)
    assert [e["event_type"] for e in body["items"]] == ["status_changed"]


async def test_events_are_only_for_staff_who_can_see_the_ticket(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])

    own_customer = await world.client.get(url(ticket), headers=world.auth("customer"))
    other_customer = await world.client.get(url(ticket), headers=world.auth("customer2"))
    assignee = await world.client.get(url(ticket), headers=world.auth("agent"))
    other_agent = await world.client.get(url(ticket), headers=world.auth("agent2"))
    admin = await world.client.get(url(ticket), headers=world.auth("admin"))

    statuses = [r.status_code for r in (own_customer, other_customer, assignee, other_agent, admin)]
    assert statuses == [403, 404, 200, 404, 200]


async def test_events_of_a_closed_ticket_can_still_be_read(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], status=TicketStatus.CLOSED)

    response = await world.client.get(url(ticket), headers=world.auth("admin"))

    assert response.status_code == 200


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 101}])
async def test_event_pagination_is_validated(world: World, params: dict[str, int]) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    response = await world.client.get(url(ticket), params=params, headers=world.auth("admin"))

    assert response.status_code == 422


async def test_events_require_authentication(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    assert (await world.client.get(url(ticket))).status_code == 401
