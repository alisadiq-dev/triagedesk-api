from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Category
from app.models.enums import Priority
from tests.support.factories import make_ticket
from tests.support.world import World

TICKETS = "/api/v1/tickets"
Param = str | int | bool


def ago(hours: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


async def ids_of(world: World, who: str, **params: Param) -> set[str]:
    response = await world.client.get(
        TICKETS, params={"page_size": 100, **params}, headers=world.auth(who)
    )
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


async def category_id(world: World, name: str) -> int:
    value = await world.session.scalar(select(Category.id).where(Category.name == name))
    assert value is not None
    return value


# --- single filters -------------------------------------------------------------------------


async def test_filter_by_priority(world: World) -> None:
    urgent = await make_ticket(world.session, world.ids["customer"], priority=Priority.URGENT)
    await make_ticket(world.session, world.ids["customer"], priority=Priority.LOW)

    assert await ids_of(world, "admin", priority="urgent") == {str(urgent.id)}


async def test_filter_by_category(world: World) -> None:
    billing = await category_id(world, "Billing")
    match = await make_ticket(world.session, world.ids["customer"], category_id=billing)
    await make_ticket(world.session, world.ids["customer"])

    assert await ids_of(world, "admin", category_id=billing) == {str(match.id)}


async def test_admin_filters_by_assignee(world: World) -> None:
    mine = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent2"])
    await make_ticket(world.session, world.ids["customer"])

    result = await ids_of(world, "admin", assignee_id=str(world.ids["agent"]))

    assert result == {str(mine.id)}


async def test_unassigned_true_and_false(world: World) -> None:
    free = await make_ticket(world.session, world.ids["customer"])
    taken = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])

    assert await ids_of(world, "admin", unassigned=True) == {str(free.id)}
    assert await ids_of(world, "admin", unassigned=False) == {str(taken.id)}


async def test_sla_breached_true_and_false_use_the_breach_rule(world: World) -> None:
    breached = await make_ticket(world.session, world.ids["customer"], first_response_due_at=ago(1))
    fine = await make_ticket(world.session, world.ids["customer"])

    assert await ids_of(world, "admin", sla_breached=True) == {str(breached.id)}
    assert await ids_of(world, "admin", sla_breached=False) == {str(fine.id)}


async def test_full_text_search_finds_words_in_title_and_description(world: World) -> None:
    in_title = await make_ticket(
        world.session, world.ids["customer"], title="Invoice missing", description="please help"
    )
    in_body = await make_ticket(
        world.session, world.ids["customer"], title="Hello", description="my invoices are wrong"
    )
    await make_ticket(world.session, world.ids["customer"], title="Printer", description="jam")

    assert await ids_of(world, "admin", q="invoice") == {str(in_title.id), str(in_body.id)}


@pytest.mark.parametrize("q", ["a & | ! ( :*", "'", "\\", "invoice'; DROP TABLE tickets;--"])
async def test_search_text_is_never_interpreted_as_query_syntax(world: World, q: str) -> None:
    await make_ticket(world.session, world.ids["customer"])

    response = await world.client.get(TICKETS, params={"q": q}, headers=world.auth("admin"))

    assert response.status_code == 200


async def test_created_after_is_inclusive_and_created_before_is_exclusive(world: World) -> None:
    old = await make_ticket(world.session, world.ids["customer"], created_at=ago(48))
    middle = await make_ticket(world.session, world.ids["customer"], created_at=ago(24))
    recent = await make_ticket(world.session, world.ids["customer"], created_at=ago(1))
    boundary = middle.created_at

    after = await ids_of(world, "admin", created_after=boundary.isoformat())
    before = await ids_of(world, "admin", created_before=boundary.isoformat())

    assert after == {str(middle.id), str(recent.id)}
    assert before == {str(old.id)}


# --- combinations, totals, pagination ---------------------------------------------------------


async def test_filters_combine_with_and_and_the_total_follows_the_filters(world: World) -> None:
    hit = await make_ticket(
        world.session, world.ids["customer"], priority=Priority.HIGH, title="Login broken"
    )
    await make_ticket(world.session, world.ids["customer"], priority=Priority.HIGH)
    await make_ticket(world.session, world.ids["customer"], title="Login broken")

    response = await world.client.get(
        TICKETS, params={"priority": "high", "q": "login"}, headers=world.auth("admin")
    )

    body = response.json()
    assert [i["id"] for i in body["items"]] == [str(hit.id)]
    assert body["total"] == 1


async def test_filtered_results_paginate(world: World) -> None:
    for _ in range(5):
        await make_ticket(world.session, world.ids["customer"], priority=Priority.URGENT)
    await make_ticket(world.session, world.ids["customer"], priority=Priority.LOW)

    response = await world.client.get(
        TICKETS,
        params={"priority": "urgent", "page": 2, "page_size": 2},
        headers=world.auth("admin"),
    )

    body = response.json()
    assert (len(body["items"]), body["total"], body["page"]) == (2, 5, 2)


# --- roles ------------------------------------------------------------------------------------


async def test_filters_never_widen_what_an_agent_can_see(world: World) -> None:
    mine = await make_ticket(
        world.session, world.ids["customer"], assignee_id=world.ids["agent"], priority=Priority.HIGH
    )
    free = await make_ticket(world.session, world.ids["customer"], priority=Priority.HIGH)
    await make_ticket(
        world.session,
        world.ids["customer"],
        assignee_id=world.ids["agent2"],
        priority=Priority.HIGH,
    )

    assert await ids_of(world, "agent", priority="high") == {str(mine.id), str(free.id)}
    assert await ids_of(world, "agent", q="printer") == {str(mine.id), str(free.id)}


async def test_customers_can_search_and_date_filter_their_own_tickets_only(world: World) -> None:
    mine = await make_ticket(world.session, world.ids["customer"], title="Invoice problem")
    await make_ticket(world.session, world.ids["customer2"], title="Invoice problem")

    assert await ids_of(world, "customer", q="invoice") == {str(mine.id)}
    assert await ids_of(world, "customer", created_after=ago(1).isoformat()) == {str(mine.id)}


@pytest.mark.parametrize(
    "params",
    [
        {"priority": "high"},
        {"category_id": 1},
        {"unassigned": True},
        {"sla_breached": True},
        {"assignee_id": "00000000-0000-0000-0000-000000000001"},
    ],
)
async def test_customers_cannot_use_staff_only_filters(
    world: World, params: dict[str, Param]
) -> None:
    response = await world.client.get(TICKETS, params=params, headers=world.auth("customer"))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_only_admins_can_filter_by_assignee(world: World) -> None:
    response = await world.client.get(
        TICKETS, params={"assignee_id": str(world.ids["agent"])}, headers=world.auth("agent")
    )

    assert response.status_code == 403


# --- validation -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"priority": "critical"},
        {"category_id": "abc"},
        {"assignee_id": "nope"},
        {"unassigned": "maybe"},
        {"sla_breached": "maybe"},
        {"q": ""},
        {"q": "x" * 201},
        {"created_after": "yesterday"},
        {"created_after": "2026-01-01T00:00:00"},  # no timezone
        {"created_before": "2026-01-01"},
    ],
)
async def test_bad_filter_values_are_validation_errors(
    world: World, params: dict[str, Param]
) -> None:
    response = await world.client.get(TICKETS, params=params, headers=world.auth("admin"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_unassigned_together_with_assignee_id_is_rejected(world: World) -> None:
    response = await world.client.get(
        TICKETS,
        params={"unassigned": True, "assignee_id": str(world.ids["agent"])},
        headers=world.auth("admin"),
    )

    assert response.status_code == 422
