from datetime import UTC, datetime, timedelta

import pytest

from app.models import Ticket
from app.models.enums import TicketStatus
from tests.support.factories import make_ticket
from tests.support.world import World

SLA_KEYS = {
    "first_response_due_at",
    "resolution_due_at",
    "first_responded_at",
    "resolved_at",
    "first_response_breached",
    "resolution_breached",
    "breached",
}


def url(ticket: Ticket) -> str:
    return f"/api/v1/tickets/{ticket.id}/sla"


def ago(hours: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


def ahead(hours: float) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


async def sla_of(world: World, ticket: Ticket) -> dict[str, object]:
    response = await world.client.get(url(ticket), headers=world.auth("admin"))
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    return body


async def test_the_response_has_exactly_the_contract_fields(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    body = await sla_of(world, ticket)

    assert set(body) == SLA_KEYS


async def test_a_ticket_inside_both_deadlines_is_not_breached(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    body = await sla_of(world, ticket)

    assert (body["first_response_breached"], body["resolution_breached"], body["breached"]) == (
        False,
        False,
        False,
    )


async def test_a_missed_first_response_deadline_is_a_first_response_breach(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], first_response_due_at=ago(1))

    body = await sla_of(world, ticket)

    assert (body["first_response_breached"], body["resolution_breached"], body["breached"]) == (
        True,
        False,
        True,
    )


async def test_a_first_response_already_given_is_not_a_breach(world: World) -> None:
    ticket = await make_ticket(
        world.session,
        world.ids["customer"],
        first_response_due_at=ago(1),
        first_responded_at=ago(2),
    )

    body = await sla_of(world, ticket)

    assert body["first_response_breached"] is False
    assert body["first_responded_at"] is not None


async def test_a_missed_resolution_deadline_is_a_resolution_breach(world: World) -> None:
    ticket = await make_ticket(
        world.session,
        world.ids["customer"],
        first_responded_at=ago(5),
        resolution_due_at=ago(1),
    )

    body = await sla_of(world, ticket)

    assert (body["first_response_breached"], body["resolution_breached"], body["breached"]) == (
        False,
        True,
        True,
    )


async def test_a_resolved_ticket_past_its_deadline_is_not_breached(world: World) -> None:
    ticket = await make_ticket(
        world.session,
        world.ids["customer"],
        status=TicketStatus.RESOLVED,
        first_responded_at=ago(5),
        first_response_due_at=ago(4),
        resolution_due_at=ago(1),
    )

    body = await sla_of(world, ticket)

    assert body["breached"] is False
    assert body["resolved_at"] is not None


async def test_a_reopened_ticket_past_its_original_deadline_shows_as_breached(
    world: World,
) -> None:
    ticket = await make_ticket(
        world.session,
        world.ids["customer"],
        status=TicketStatus.IN_PROGRESS,
        first_responded_at=ago(5),
        resolution_due_at=ago(1),
    )

    body = await sla_of(world, ticket)

    assert (body["resolved_at"], body["resolution_breached"]) == (None, True)


async def test_the_breach_flag_agrees_with_the_staff_ticket(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], first_response_due_at=ago(1))

    staff = await world.client.get(f"/api/v1/tickets/{ticket.id}", headers=world.auth("admin"))

    assert staff.json()["sla_breached"] is (await sla_of(world, ticket))["breached"]


async def test_sla_status_is_only_for_staff_who_can_see_the_ticket(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])

    responses = [
        await world.client.get(url(ticket), headers=world.auth(name))
        for name in ("customer", "customer2", "agent", "agent2", "admin")
    ]

    assert [r.status_code for r in responses] == [403, 404, 200, 404, 200]


async def test_an_unassigned_ticket_is_visible_to_any_agent(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    response = await world.client.get(url(ticket), headers=world.auth("agent2"))

    assert response.status_code == 200


async def test_the_sla_of_a_closed_ticket_can_still_be_read(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], status=TicketStatus.CLOSED)

    assert (await world.client.get(url(ticket), headers=world.auth("admin"))).status_code == 200


async def test_sla_requires_authentication(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    assert (await world.client.get(url(ticket))).status_code == 401


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "123"])
async def test_a_malformed_ticket_id_is_a_validation_error(world: World, bad_id: str) -> None:
    response = await world.client.get(f"/api/v1/tickets/{bad_id}/sla", headers=world.auth("admin"))

    assert response.status_code == 422


async def test_a_public_agent_comment_stops_the_first_response_clock_but_a_note_does_not(
    world: World,
) -> None:
    ticket = await make_ticket(
        world.session,
        world.ids["customer"],
        assignee_id=world.ids["agent"],
        first_response_due_at=ago(1),
    )
    comments = f"/api/v1/tickets/{ticket.id}/comments"
    await world.client.post(
        comments, json={"body": "note", "is_internal": True}, headers=world.auth("agent")
    )
    after_note = await sla_of(world, ticket)

    await world.client.post(comments, json={"body": "hello"}, headers=world.auth("agent"))
    after_reply = await sla_of(world, ticket)

    assert (after_note["first_responded_at"], after_note["first_response_breached"]) == (None, True)
    assert after_reply["first_responded_at"] is not None
    assert after_reply["first_response_breached"] is False
