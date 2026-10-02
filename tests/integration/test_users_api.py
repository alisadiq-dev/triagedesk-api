import logging
import uuid

import pytest

from app.core.logging import JsonFormatter
from app.models import Profile
from app.models.enums import Role, TicketStatus
from tests.support.factories import make_ticket
from tests.support.world import World


class CapturingHandler(logging.Handler):
    """Formats at emit time, while the request id is still set."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


async def test_admin_lists_users_with_the_page_shape(world: World) -> None:
    response = await world.client.get("/api/v1/users", headers=world.auth("admin"))

    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"items", "page", "page_size", "total"}
    assert body["total"] == 5
    assert {item["email"] for item in body["items"]} >= {"agent@example.com", "admin@example.com"}
    assert set(body["items"][0]) == {"id", "email", "role", "created_at"}


async def test_users_list_can_filter_by_role_and_paginate(world: World) -> None:
    response = await world.client.get(
        "/api/v1/users",
        params={"role": "agent", "page_size": 1, "page": 2},
        headers=world.auth("admin"),
    )

    body = response.json()
    assert body["total"] == 2
    assert body["page"] == 2
    assert len(body["items"]) == 1
    assert body["items"][0]["role"] == "agent"


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 101}, {"role": "root"}])
async def test_users_list_rejects_bad_query_values(
    world: World, params: dict[str, str | int]
) -> None:
    response = await world.client.get("/api/v1/users", params=params, headers=world.auth("admin"))

    assert response.status_code == 422


@pytest.mark.parametrize("name", ["customer", "agent"])
async def test_only_admins_can_use_the_user_endpoints(world: World, name: str) -> None:
    target = world.ids["customer2"]

    listed = await world.client.get("/api/v1/users", headers=world.auth(name))
    fetched = await world.client.get(f"/api/v1/users/{target}", headers=world.auth(name))
    patched = await world.client.patch(
        f"/api/v1/users/{target}", json={"role": "admin"}, headers=world.auth(name)
    )

    assert (listed.status_code, fetched.status_code, patched.status_code) == (403, 403, 403)
    assert patched.json()["error"]["code"] == "forbidden"


async def test_admin_gets_one_user_or_404(world: World) -> None:
    found = await world.client.get(
        f"/api/v1/users/{world.ids['agent']}", headers=world.auth("admin")
    )
    missing = await world.client.get(f"/api/v1/users/{uuid.uuid4()}", headers=world.auth("admin"))
    malformed = await world.client.get("/api/v1/users/not-a-uuid", headers=world.auth("admin"))

    assert found.json()["role"] == "agent"
    assert missing.status_code == 404
    assert malformed.status_code == 422


async def test_admin_changes_a_role(world: World) -> None:
    target = world.ids["customer2"]

    response = await world.client.patch(
        f"/api/v1/users/{target}", json={"role": "agent"}, headers=world.auth("admin")
    )

    assert response.status_code == 200
    assert response.json()["role"] == "agent"
    world.session.expire_all()
    profile = await world.session.get(Profile, target)
    assert profile is not None
    assert profile.role == Role.AGENT


@pytest.mark.parametrize("body", [{"role": "root"}, {}, {"role": "agent", "email": "x@y.z"}])
async def test_role_change_validates_the_body(world: World, body: dict[str, str]) -> None:
    response = await world.client.patch(
        f"/api/v1/users/{world.ids['customer2']}", json=body, headers=world.auth("admin")
    )

    assert response.status_code == 422


async def test_unknown_user_role_change_is_404(world: World) -> None:
    response = await world.client.patch(
        f"/api/v1/users/{uuid.uuid4()}", json={"role": "agent"}, headers=world.auth("admin")
    )

    assert response.status_code == 404


async def test_an_admin_cannot_change_their_own_role(world: World) -> None:
    response = await world.client.patch(
        f"/api/v1/users/{world.ids['admin']}",
        json={"role": "customer"},
        headers=world.auth("admin"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "cannot_change_own_role"


async def test_an_agent_with_open_assigned_tickets_cannot_become_a_customer(world: World) -> None:
    await make_ticket(
        world.session,
        world.ids["customer"],
        status=TicketStatus.IN_PROGRESS,
        assignee_id=world.ids["agent"],
    )

    response = await world.client.patch(
        f"/api/v1/users/{world.ids['agent']}",
        json={"role": "customer"},
        headers=world.auth("admin"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "role_change_blocked"


async def test_an_agent_with_only_closed_tickets_can_become_a_customer(world: World) -> None:
    await make_ticket(
        world.session,
        world.ids["customer"],
        status=TicketStatus.CLOSED,
        assignee_id=world.ids["agent"],
    )

    response = await world.client.patch(
        f"/api/v1/users/{world.ids['agent']}",
        json={"role": "customer"},
        headers=world.auth("admin"),
    )

    assert response.status_code == 200


async def test_a_role_change_writes_one_structured_log_line_with_the_request_id(
    world: World,
) -> None:
    handler = CapturingHandler()
    audit = logging.getLogger("app.audit")
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    try:
        await world.client.patch(
            f"/api/v1/users/{world.ids['customer2']}",
            json={"role": "agent"},
            headers={**world.auth("admin"), "X-Request-ID": "req-role-1"},
        )
    finally:
        audit.removeHandler(handler)

    import json

    assert len(handler.lines) == 1
    entry = json.loads(handler.lines[0])
    assert entry["message"] == "role_changed"
    assert entry["actor_id"] == str(world.ids["admin"])
    assert entry["target_id"] == str(world.ids["customer2"])
    assert entry["old_role"] == "customer"
    assert entry["new_role"] == "agent"
    assert entry["request_id"] == "req-role-1"


async def test_changing_to_the_same_role_is_a_no_op_without_a_log_line(world: World) -> None:
    handler = CapturingHandler()
    audit = logging.getLogger("app.audit")
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    try:
        response = await world.client.patch(
            f"/api/v1/users/{world.ids['agent']}",
            json={"role": "agent"},
            headers=world.auth("admin"),
        )
    finally:
        audit.removeHandler(handler)

    assert response.status_code == 200
    assert handler.lines == []


async def test_a_non_admin_cannot_change_roles_even_with_a_role_in_the_body(world: World) -> None:
    response = await world.client.patch(
        f"/api/v1/users/{world.ids['customer']}",
        json={"role": "admin"},
        headers=world.auth("customer"),
    )

    assert response.status_code == 403
    world.session.expire_all()
    profile = await world.session.get(Profile, world.ids["customer"])
    assert profile is not None
    assert profile.role == Role.CUSTOMER
