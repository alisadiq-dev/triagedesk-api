import httpx2
import pytest
from sqlalchemy import select

from app.models import Ticket
from app.models.enums import TicketStatus
from tests.support.factories import make_ticket
from tests.support.world import World

CUSTOMER_COMMENT_KEYS = {"id", "author_type", "body", "created_at"}
STAFF_COMMENT_KEYS = {"id", "author_id", "author_role", "is_internal", "body", "created_at"}


def url(ticket: Ticket) -> str:
    return f"/api/v1/tickets/{ticket.id}/comments"


async def post(
    world: World, who: str, ticket: Ticket, body: str = "hello", **extra: object
) -> httpx2.Response:
    return await world.client.post(
        url(ticket), json={"body": body, **extra}, headers=world.auth(who)
    )


async def first_responded_at(world: World, ticket: Ticket) -> object:
    # A column select always reads the database; expire_all would expire `ticket` for later calls.
    return await world.session.scalar(
        select(Ticket.first_responded_at).where(Ticket.id == ticket.id)
    )


async def test_customer_adds_a_public_comment_and_sees_only_allowlisted_fields(
    world: World,
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    response = await post(world, "customer", ticket, "More details")

    assert response.status_code == 201
    assert set(response.json()) == CUSTOMER_COMMENT_KEYS
    assert response.json()["author_type"] == "customer"


async def test_customers_cannot_write_internal_notes(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    response = await post(world, "customer", ticket, is_internal=True)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_a_customer_cannot_comment_on_someone_elses_ticket(world: World) -> None:
    other = await make_ticket(world.session, world.ids["customer2"])

    response = await post(world, "customer", other)

    assert response.status_code == 404


async def test_the_assignee_adds_a_public_comment_which_counts_as_the_first_response(
    world: World,
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])

    response = await post(world, "agent", ticket, "We are on it")

    assert response.status_code == 201
    assert set(response.json()) == STAFF_COMMENT_KEYS
    assert response.json()["author_role"] == "agent"
    assert response.json()["is_internal"] is False
    assert await first_responded_at(world, ticket) is not None


async def test_the_first_response_time_is_set_once(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    await post(world, "agent", ticket, "first")
    first = await first_responded_at(world, ticket)

    await post(world, "agent", ticket, "second")

    assert await first_responded_at(world, ticket) == first


async def test_internal_notes_and_customer_comments_are_not_a_first_response(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])

    await post(world, "agent", ticket, "private", is_internal=True)
    await post(world, "customer", ticket, "any news?")

    assert await first_responded_at(world, ticket) is None


async def test_an_admin_can_comment_on_any_open_ticket_and_it_counts(world: World) -> None:
    ticket = await make_ticket(
        world.session, world.ids["customer"], assignee_id=world.ids["agent2"]
    )

    response = await post(world, "admin", ticket, "Admin here")

    assert response.status_code == 201
    assert await first_responded_at(world, ticket) is not None


async def test_an_agent_must_be_the_assignee_to_comment(world: World) -> None:
    unassigned = await make_ticket(world.session, world.ids["customer"])
    others = await make_ticket(
        world.session, world.ids["customer"], assignee_id=world.ids["agent2"]
    )

    on_unassigned = await post(world, "agent", unassigned)
    on_others = await post(world, "agent", others)

    assert (on_unassigned.status_code, on_others.status_code) == (403, 404)


async def test_nobody_can_comment_on_a_closed_ticket(world: World) -> None:
    ticket = await make_ticket(
        world.session,
        world.ids["customer"],
        assignee_id=world.ids["agent"],
        status=TicketStatus.CLOSED,
    )

    results = [await post(world, who, ticket) for who in ("customer", "agent", "admin")]
    internal = await post(world, "agent", ticket, is_internal=True)

    assert [r.status_code for r in results] == [409, 409, 409]
    assert internal.status_code == 409
    assert results[0].json()["error"]["code"] == "ticket_closed"


async def test_a_customer_comment_does_not_change_the_ticket_status(world: World) -> None:
    ticket = await make_ticket(
        world.session,
        world.ids["customer"],
        status=TicketStatus.WAITING_ON_CUSTOMER,
        assignee_id=world.ids["agent"],
    )

    await post(world, "customer", ticket, "here is the info")

    ticket_id = ticket.id
    world.session.expire_all()
    status = await world.session.scalar(select(Ticket.status).where(Ticket.id == ticket_id))
    assert status == TicketStatus.WAITING_ON_CUSTOMER


@pytest.mark.parametrize(
    "body",
    [{}, {"body": ""}, {"body": "   "}, {"body": "x" * 10001}, {"body": "ok", "author_id": "x"}],
)
async def test_comment_bodies_are_validated(world: World, body: dict[str, object]) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    response = await world.client.post(url(ticket), json=body, headers=world.auth("customer"))

    assert response.status_code == 422


async def test_comment_text_is_trimmed(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    response = await post(world, "customer", ticket, "  padded  ")

    assert response.json()["body"] == "padded"


# --- list -----------------------------------------------------------------------------------


async def build_conversation(world: World) -> Ticket:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    await post(world, "customer", ticket, "1 customer")
    await post(world, "agent", ticket, "2 agent public")
    await post(world, "agent", ticket, "3 agent internal", is_internal=True)
    await post(world, "admin", ticket, "4 admin public")
    return ticket


async def test_customers_see_public_comments_only_without_author_identity(world: World) -> None:
    ticket = await build_conversation(world)

    response = await world.client.get(url(ticket), headers=world.auth("customer"))

    body = response.json()
    assert response.status_code == 200
    assert [c["body"] for c in body["items"]] == ["1 customer", "2 agent public", "4 admin public"]
    assert [c["author_type"] for c in body["items"]] == ["customer", "support", "support"]
    assert all(set(c) == CUSTOMER_COMMENT_KEYS for c in body["items"])
    assert body["total"] == 3


@pytest.mark.parametrize("who", ["agent", "admin"])
async def test_staff_see_every_comment_including_internal_notes(world: World, who: str) -> None:
    ticket = await build_conversation(world)

    response = await world.client.get(url(ticket), headers=world.auth(who))

    body = response.json()
    assert [c["body"] for c in body["items"]][2] == "3 agent internal"
    assert [c["is_internal"] for c in body["items"]] == [False, False, True, False]
    assert all(set(c) == STAFF_COMMENT_KEYS for c in body["items"])


async def test_comment_listing_is_paginated_oldest_first(world: World) -> None:
    ticket = await build_conversation(world)

    response = await world.client.get(
        url(ticket), params={"page": 2, "page_size": 3}, headers=world.auth("admin")
    )

    body = response.json()
    assert (body["page"], body["page_size"], body["total"]) == (2, 3, 4)
    assert [c["body"] for c in body["items"]] == ["4 admin public"]


async def test_listing_comments_follows_ticket_visibility(world: World) -> None:
    others = await make_ticket(
        world.session, world.ids["customer"], assignee_id=world.ids["agent2"]
    )
    theirs = await make_ticket(world.session, world.ids["customer2"])

    agent = await world.client.get(url(others), headers=world.auth("agent"))
    customer = await world.client.get(url(theirs), headers=world.auth("customer"))

    assert (agent.status_code, customer.status_code) == (404, 404)


async def test_comments_require_authentication(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    assert (await world.client.get(url(ticket))).status_code == 401
    assert (await world.client.post(url(ticket), json={"body": "x"})).status_code == 401
