import pytest
from sqlalchemy import select

from app.models import SlaPolicy
from app.models.enums import Priority
from tests.support.factories import make_ticket
from tests.support.world import World

CATEGORIES = "/api/v1/categories"
SLA = "/api/v1/sla-policies"


async def test_staff_list_active_categories(world: World) -> None:
    response = await world.client.get(CATEGORIES, headers=world.auth("agent"))

    names = {item["name"] for item in response.json()}
    assert response.status_code == 200
    assert names == {
        "Billing",
        "Technical Issue",
        "Account Access",
        "Feature Request",
        "General Inquiry",
    }
    assert set(response.json()[0]) == {"id", "name", "description", "is_active"}


async def test_only_admins_can_see_inactive_categories(world: World) -> None:
    created = await world.client.post(
        CATEGORIES, json={"name": "Legacy"}, headers=world.auth("admin")
    )
    await world.client.patch(
        f"{CATEGORIES}/{created.json()['id']}",
        json={"is_active": False},
        headers=world.auth("admin"),
    )

    admin_view = await world.client.get(
        CATEGORIES, params={"include_inactive": "true"}, headers=world.auth("admin")
    )
    agent_view = await world.client.get(
        CATEGORIES, params={"include_inactive": "true"}, headers=world.auth("agent")
    )
    default_view = await world.client.get(CATEGORIES, headers=world.auth("admin"))

    assert "Legacy" in {c["name"] for c in admin_view.json()}
    assert "Legacy" not in {c["name"] for c in agent_view.json()}
    assert "Legacy" not in {c["name"] for c in default_view.json()}


async def test_admin_creates_a_category_and_the_name_is_trimmed(world: World) -> None:
    response = await world.client.post(
        CATEGORIES,
        json={"name": "  Shipping  ", "description": "Delivery problems"},
        headers=world.auth("admin"),
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Shipping"
    assert response.json()["is_active"] is True


async def test_duplicate_category_names_conflict_ignoring_case(world: World) -> None:
    response = await world.client.post(
        CATEGORIES, json={"name": "BILLING"}, headers=world.auth("admin")
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "name_taken"


@pytest.mark.parametrize(
    "body", [{}, {"name": ""}, {"name": "   "}, {"name": "x" * 101}, {"name": "ok", "extra": 1}]
)
async def test_category_creation_validates_the_body(world: World, body: dict[str, object]) -> None:
    response = await world.client.post(CATEGORIES, json=body, headers=world.auth("admin"))

    assert response.status_code == 422


async def test_admin_renames_and_deactivates_a_category(world: World) -> None:
    categories = (await world.client.get(CATEGORIES, headers=world.auth("admin"))).json()
    billing = next(c for c in categories if c["name"] == "Billing")

    renamed = await world.client.patch(
        f"{CATEGORIES}/{billing['id']}", json={"name": "Payments"}, headers=world.auth("admin")
    )
    deactivated = await world.client.patch(
        f"{CATEGORIES}/{billing['id']}", json={"is_active": False}, headers=world.auth("admin")
    )

    assert renamed.json()["name"] == "Payments"
    assert deactivated.json()["is_active"] is False
    assert deactivated.json()["name"] == "Payments"


async def test_renaming_to_an_existing_name_conflicts(world: World) -> None:
    categories = (await world.client.get(CATEGORIES, headers=world.auth("admin"))).json()
    billing = next(c for c in categories if c["name"] == "Billing")

    response = await world.client.patch(
        f"{CATEGORIES}/{billing['id']}",
        json={"name": "technical issue"},
        headers=world.auth("admin"),
    )

    assert response.status_code == 409


@pytest.mark.parametrize("body", [{}, {"unknown": 1}, {"name": ""}])
async def test_category_patch_needs_at_least_one_valid_field(
    world: World, body: dict[str, object]
) -> None:
    categories = (await world.client.get(CATEGORIES, headers=world.auth("admin"))).json()

    response = await world.client.patch(
        f"{CATEGORIES}/{categories[0]['id']}", json=body, headers=world.auth("admin")
    )

    assert response.status_code == 422


async def test_patching_an_unknown_category_is_404(world: World) -> None:
    response = await world.client.patch(
        f"{CATEGORIES}/999999", json={"name": "Nope"}, headers=world.auth("admin")
    )

    assert response.status_code == 404


async def test_customers_and_agents_cannot_change_categories(world: World) -> None:
    for name in ("customer", "agent"):
        created = await world.client.post(CATEGORIES, json={"name": "X"}, headers=world.auth(name))
        patched = await world.client.patch(
            CATEGORIES + "/1", json={"name": "Y"}, headers=world.auth(name)
        )
        assert (created.status_code, patched.status_code) == (403, 403)


async def test_customers_cannot_list_categories(world: World) -> None:
    response = await world.client.get(CATEGORIES, headers=world.auth("customer"))

    assert response.status_code == 403


async def test_staff_list_the_four_sla_policies_most_urgent_first(world: World) -> None:
    response = await world.client.get(SLA, headers=world.auth("agent"))

    assert response.status_code == 200
    assert [p["priority"] for p in response.json()] == ["urgent", "high", "medium", "low"]
    assert response.json()[0] == {
        "priority": "urgent",
        "response_hours": 1,
        "resolution_hours": 4,
        "updated_at": response.json()[0]["updated_at"],
    }


async def test_customers_cannot_list_sla_policies(world: World) -> None:
    response = await world.client.get(SLA, headers=world.auth("customer"))

    assert response.status_code == 403


async def test_admin_updates_a_policy_and_it_records_who(world: World) -> None:
    response = await world.client.patch(
        f"{SLA}/high", json={"response_hours": 2}, headers=world.auth("admin")
    )

    assert response.status_code == 200
    assert response.json()["response_hours"] == 2
    assert response.json()["resolution_hours"] == 24
    world.session.expire_all()
    policy = await world.session.scalar(
        select(SlaPolicy).where(SlaPolicy.priority == Priority.HIGH)
    )
    assert policy is not None
    assert policy.updated_by == world.ids["admin"]


@pytest.mark.parametrize(
    "body",
    [
        {"resolution_hours": 2},  # high has response 4, so resolution 2 < 4
        {"response_hours": 30},  # response 30 > resolution 24
        {"response_hours": 0},
        {"resolution_hours": -1},
        {},
        {"priority": "low"},
    ],
)
async def test_policy_updates_are_validated_after_merging(
    world: World, body: dict[str, int | str]
) -> None:
    response = await world.client.patch(f"{SLA}/high", json=body, headers=world.auth("admin"))

    assert response.status_code == 422


async def test_unknown_priority_in_the_path_is_422(world: World) -> None:
    response = await world.client.patch(
        f"{SLA}/critical", json={"response_hours": 2}, headers=world.auth("admin")
    )

    assert response.status_code == 422


async def test_policy_edits_never_rewrite_existing_ticket_deadlines(world: World) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])
    before = ticket.first_response_due_at

    await world.client.patch(
        f"{SLA}/medium", json={"response_hours": 1}, headers=world.auth("admin")
    )

    world.session.expire_all()
    await world.session.refresh(ticket)
    assert ticket.first_response_due_at == before


@pytest.mark.parametrize("name", ["customer", "agent"])
async def test_only_admins_can_update_policies(world: World, name: str) -> None:
    response = await world.client.patch(
        f"{SLA}/high", json={"response_hours": 2}, headers=world.auth(name)
    )

    assert response.status_code == 403
