import uuid
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import select

from app.models import Ticket, TicketEvent
from app.models.enums import AiStatus, EventType, Priority, PrioritySource, TicketStatus
from tests.support.ai import NoTriage
from tests.support.factories import make_ticket
from tests.support.world import World

TICKETS = "/api/v1/tickets"
CUSTOMER_KEYS = {"id", "title", "description", "status", "created_at", "updated_at"}
STAFF_KEYS = CUSTOMER_KEYS | {
    "customer_id", "customer_email", "assignee_id", "category_id", "category_source", "priority",
    "priority_source", "sentiment", "ai_status", "ai_suggested_reply", "ai_model",
    "ai_prompt_version", "first_response_due_at", "resolution_due_at", "first_responded_at",
    "resolved_at", "sla_breached",
}  # fmt: skip


async def create_ticket(world: World, who: str = "customer", /, **body: object) -> httpx2.Response:
    payload = {"title": "Printer is broken", "description": "Blank pages only"} | body
    return await world.client.post(TICKETS, json=payload, headers=world.auth(who))


# --- create ---------------------------------------------------------------------------------


async def test_customer_creates_a_ticket_and_sees_only_allowlisted_fields(world: World) -> None:
    response = await create_ticket(world)

    assert response.status_code == 201
    assert set(response.json()) == CUSTOMER_KEYS
    assert response.json()["status"] == "open"


async def test_a_new_ticket_gets_defaults_sla_deadlines_and_a_created_event(world: World) -> None:
    world.app.state.triage_runner = NoTriage()  # look at the ticket exactly as created
    response = await create_ticket(world)

    ticket = await world.session.get(Ticket, uuid.UUID(response.json()["id"]))
    assert ticket is not None
    assert ticket.customer_id == world.ids["customer"]
    assert ticket.priority == Priority.MEDIUM
    assert ticket.priority_source == PrioritySource.DEFAULT
    assert ticket.ai_status == AiStatus.PENDING
    assert ticket.first_response_due_at == ticket.created_at + timedelta(hours=8)
    assert ticket.resolution_due_at == ticket.created_at + timedelta(hours=72)
    events = (
        await world.session.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id))
    ).all()
    assert [(e.event_type, e.actor_id, e.to_value) for e in events] == [
        (EventType.TICKET_CREATED, world.ids["customer"], "open")
    ]


async def test_title_and_description_are_trimmed(world: World) -> None:
    response = await create_ticket(world, title="  Spaced  ", description="  body  ")

    assert response.json()["title"] == "Spaced"
    assert response.json()["description"] == "body"


@pytest.mark.parametrize(
    "body",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"description": ""},
        {"description": "x" * 10001},
        {"priority": "urgent"},
        {"category_id": 1},
        {"status": "closed"},
    ],
)
async def test_ticket_creation_validates_the_body(world: World, body: dict[str, object]) -> None:
    response = await create_ticket(world, **body)

    assert response.status_code == 422


async def test_missing_fields_are_rejected(world: World) -> None:
    response = await world.client.post(
        TICKETS, json={"title": "only a title"}, headers=world.auth("customer")
    )

    assert response.status_code == 422


@pytest.mark.parametrize("who", ["agent", "admin"])
async def test_only_customers_create_tickets(world: World, who: str) -> None:
    response = await create_ticket(world, who)

    assert response.status_code == 403


async def test_creating_a_ticket_requires_authentication(world: World) -> None:
    response = await world.client.post(TICKETS, json={"title": "t", "description": "d"})

    assert response.status_code == 401


# --- get ------------------------------------------------------------------------------------


async def test_customer_reads_own_ticket_with_the_allowlist_only(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])

    response = await world.client.get(f"{TICKETS}/{ticket.id}", headers=world.auth("customer"))

    assert response.status_code == 200
    assert set(response.json()) == CUSTOMER_KEYS


async def test_customer_gets_404_for_someone_elses_ticket_and_for_a_missing_one(
    world: World,
) -> None:
    other = await make_ticket(world.session, world.ids["customer2"])

    someone_elses = await world.client.get(f"{TICKETS}/{other.id}", headers=world.auth("customer"))
    missing = await world.client.get(f"{TICKETS}/{uuid.uuid4()}", headers=world.auth("customer"))

    assert someone_elses.status_code == missing.status_code == 404
    assert someone_elses.json() == missing.json()


async def test_malformed_ticket_id_is_422(world: World) -> None:
    response = await world.client.get(f"{TICKETS}/nope", headers=world.auth("admin"))

    assert response.status_code == 422


async def test_agent_sees_unassigned_and_own_tickets_with_staff_fields(world: World) -> None:
    unassigned = await make_ticket(world.session, world.ids["customer"])
    mine = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])

    first = await world.client.get(f"{TICKETS}/{unassigned.id}", headers=world.auth("agent"))
    second = await world.client.get(f"{TICKETS}/{mine.id}", headers=world.auth("agent"))

    assert first.status_code == second.status_code == 200
    assert set(first.json()) == STAFF_KEYS
    assert first.json()["customer_email"] == "customer@example.com"
    assert first.json()["sla_breached"] is False


async def test_agent_gets_404_for_a_ticket_assigned_to_another_agent(world: World) -> None:
    theirs = await make_ticket(
        world.session, world.ids["customer"], assignee_id=world.ids["agent2"]
    )

    response = await world.client.get(f"{TICKETS}/{theirs.id}", headers=world.auth("agent"))

    assert response.status_code == 404


async def test_admin_reads_any_ticket(world: World) -> None:
    theirs = await make_ticket(
        world.session, world.ids["customer"], assignee_id=world.ids["agent2"]
    )

    response = await world.client.get(f"{TICKETS}/{theirs.id}", headers=world.auth("admin"))

    assert response.status_code == 200
    assert response.json()["assignee_id"] == str(world.ids["agent2"])


async def test_sla_breached_is_reported_to_staff(world: World) -> None:
    overdue = await make_ticket(
        world.session,
        world.ids["customer"],
        first_response_due_at=datetime.now(UTC) - timedelta(hours=1),
    )

    response = await world.client.get(f"{TICKETS}/{overdue.id}", headers=world.auth("admin"))

    assert response.json()["sla_breached"] is True


# --- list -----------------------------------------------------------------------------------


async def seed_listing(world: World) -> dict[str, Ticket]:
    return {
        "c1_open": await make_ticket(world.session, world.ids["customer"], title="c1 open"),
        "c1_mine": await make_ticket(
            world.session, world.ids["customer"], title="c1 assigned a1",
            assignee_id=world.ids["agent"], status=TicketStatus.IN_PROGRESS,
        ),
        "c2_theirs": await make_ticket(
            world.session, world.ids["customer2"], title="c2 assigned a2",
            assignee_id=world.ids["agent2"], status=TicketStatus.IN_PROGRESS,
        ),
        "c2_open": await make_ticket(world.session, world.ids["customer2"], title="c2 open"),
    }  # fmt: skip


async def titles(world: World, who: str, **params: str | int) -> list[str]:
    response = await world.client.get(TICKETS, params=params, headers=world.auth(who))
    assert response.status_code == 200, response.text
    return [item["title"] for item in response.json()["items"]]


async def test_customers_list_only_their_own_tickets_with_the_allowlist(world: World) -> None:
    await seed_listing(world)

    response = await world.client.get(TICKETS, headers=world.auth("customer"))

    body = response.json()
    assert body["total"] == 2
    assert {t["title"] for t in body["items"]} == {"c1 open", "c1 assigned a1"}
    assert all(set(item) == CUSTOMER_KEYS for item in body["items"])


async def test_agents_list_unassigned_and_their_own_but_not_other_agents_tickets(
    world: World,
) -> None:
    await seed_listing(world)

    assert set(await titles(world, "agent")) == {"c1 open", "c1 assigned a1", "c2 open"}
    assert set(await titles(world, "agent2")) == {"c1 open", "c2 assigned a2", "c2 open"}


async def test_admins_list_everything_with_staff_fields(world: World) -> None:
    await seed_listing(world)

    response = await world.client.get(TICKETS, headers=world.auth("admin"))

    assert response.json()["total"] == 4
    assert all(set(item) == STAFF_KEYS for item in response.json()["items"])


async def test_list_filters_by_status(world: World) -> None:
    await seed_listing(world)

    assert set(await titles(world, "admin", status="in_progress")) == {
        "c1 assigned a1",
        "c2 assigned a2",
    }
    assert set(await titles(world, "customer", status="open")) == {"c1 open"}


async def test_list_pagination_uses_the_shared_page_shape(world: World) -> None:
    await seed_listing(world)

    response = await world.client.get(
        TICKETS, params={"page": 2, "page_size": 3}, headers=world.auth("admin")
    )

    body = response.json()
    assert (body["page"], body["page_size"], body["total"]) == (2, 3, 4)
    assert len(body["items"]) == 1


async def test_a_page_past_the_end_is_empty_but_keeps_the_total(world: World) -> None:
    await seed_listing(world)

    response = await world.client.get(TICKETS, params={"page": 9}, headers=world.auth("admin"))

    assert response.json()["items"] == []
    assert response.json()["total"] == 4


async def test_default_sort_is_newest_first_and_can_be_reversed(world: World) -> None:
    await seed_listing(world)

    newest_first = await titles(world, "admin")
    oldest_first = await titles(world, "admin", sort="created_at")

    assert newest_first == oldest_first[::-1]
    assert oldest_first[0] == "c1 open"


async def test_staff_can_sort_by_sla_deadlines_but_customers_cannot(world: World) -> None:
    await seed_listing(world)

    staff = await world.client.get(
        TICKETS, params={"sort": "first_response_due_at"}, headers=world.auth("admin")
    )
    customer = await world.client.get(
        TICKETS, params={"sort": "resolution_due_at"}, headers=world.auth("customer")
    )

    assert staff.status_code == 200
    assert customer.status_code == 422


@pytest.mark.parametrize(
    "params", [{"status": "done"}, {"sort": "title"}, {"page": 0}, {"page_size": 0}]
)
async def test_list_rejects_bad_query_values(world: World, params: dict[str, str | int]) -> None:
    response = await world.client.get(TICKETS, params=params, headers=world.auth("admin"))

    assert response.status_code == 422
