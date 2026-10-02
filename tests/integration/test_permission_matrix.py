"""Every role on every /api/v1 route (CONSTRAINTS.md #4). A meta-test fails when a route is missing.

Roles: customer owns the ticket, customer2 is another customer, agent is the assignee (unless the
scenario is "unassigned"), agent2 is another agent, admin is the admin.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import select

from app.models import Category
from app.models.enums import TicketStatus
from tests.support.factories import make_ticket
from tests.support.world import World

ROLES = ["customer", "customer2", "agent", "agent2", "admin"]
Body = dict[str, str | bool | int] | None


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    body: Body
    scenario: str  # "assigned" (to agent, in progress) or "unassigned" (open)
    statuses: tuple[int, int, int, int, int]  # in ROLES order


def route(method: str, path: str, body: Body, scenario: str, *statuses: int) -> Route:
    assert len(statuses) == len(ROLES)
    return Route(
        method,
        path,
        body,
        scenario,
        (statuses[0], statuses[1], statuses[2], statuses[3], statuses[4]),
    )


MATRIX = [
    route("GET", "/api/v1/me", None, "assigned", 200, 200, 200, 200, 200),
    route("GET", "/api/v1/users", None, "assigned", 403, 403, 403, 403, 200),
    route("GET", "/api/v1/users/{user_id}", None, "assigned", 403, 403, 403, 403, 200),
    route(
        "PATCH",
        "/api/v1/users/{user_id}",
        {"role": "customer"},
        "assigned",
        403,
        403,
        403,
        403,
        200,
    ),
    route("GET", "/api/v1/categories", None, "assigned", 403, 403, 200, 200, 200),
    route("POST", "/api/v1/categories", {"name": "Shipping"}, "assigned", 403, 403, 403, 403, 201),
    route(
        "PATCH",
        "/api/v1/categories/{category_id}",
        {"is_active": True},
        "assigned",
        403,
        403,
        403,
        403,
        200,
    ),
    route("GET", "/api/v1/sla-policies", None, "assigned", 403, 403, 200, 200, 200),
    route(
        "PATCH",
        "/api/v1/sla-policies/{priority}",
        {"response_hours": 4},
        "assigned",
        403,
        403,
        403,
        403,
        200,
    ),
    route(
        "POST",
        "/api/v1/tickets",
        {"title": "t", "description": "d"},
        "assigned",
        201,
        201,
        403,
        403,
        403,
    ),
    route("GET", "/api/v1/tickets", None, "assigned", 200, 200, 200, 200, 200),
    route("GET", "/api/v1/tickets/{ticket_id}", None, "assigned", 200, 404, 200, 404, 200),
    route(
        "PATCH",
        "/api/v1/tickets/{ticket_id}",
        {"priority": "high"},
        "assigned",
        403,
        404,
        200,
        404,
        200,
    ),
    route("POST", "/api/v1/tickets/{ticket_id}/claim", None, "unassigned", 403, 404, 200, 200, 200),
    route("POST", "/api/v1/tickets/{ticket_id}/release", None, "assigned", 403, 404, 200, 404, 200),
    route(
        "PUT",
        "/api/v1/tickets/{ticket_id}/assignee",
        {"assignee_id": "{agent2_id}"},
        "assigned",
        403,
        404,
        403,
        404,
        200,
    ),
    route("GET", "/api/v1/tickets/{ticket_id}/comments", None, "assigned", 200, 404, 200, 404, 200),
    route(
        "POST",
        "/api/v1/tickets/{ticket_id}/comments",
        {"body": "hello"},
        "assigned",
        201,
        404,
        201,
        404,
        201,
    ),
]

CASES = [
    pytest.param(r, role, status, id=f"{r.method} {r.path} as {role}")
    for r in MATRIX
    for role, status in zip(ROLES, r.statuses, strict=True)
]


async def prepare(world: World, entry: Route) -> tuple[str, Body]:
    """Create the scenario's ticket and substitute placeholders in the path and body."""
    assigned = entry.scenario == "assigned"
    ticket = await make_ticket(
        world.session,
        world.ids["customer"],
        assignee_id=world.ids["agent"] if assigned else None,
        status=TicketStatus.IN_PROGRESS if assigned else TicketStatus.OPEN,
    )
    category_id = await world.session.scalar(select(Category.id).limit(1))
    values = {
        "{ticket_id}": str(ticket.id),
        "{user_id}": str(world.ids["customer2"]),
        "{category_id}": str(category_id),
        "{priority}": "high",
        "{agent2_id}": str(world.ids["agent2"]),
    }

    def substitute(text: str) -> str:
        for placeholder, value in values.items():
            text = text.replace(placeholder, value)
        return text

    body = None
    if entry.body is not None:
        body = {k: substitute(v) if isinstance(v, str) else v for k, v in entry.body.items()}
    return substitute(entry.path), body


@pytest.mark.parametrize(("entry", "role", "status"), CASES)
async def test_each_role_gets_the_expected_status_on_each_route(
    world: World, entry: Route, role: str, status: int
) -> None:
    url, body = await prepare(world, entry)

    response = await world.client.request(entry.method, url, json=body, headers=world.auth(role))

    assert response.status_code == status, response.text


@pytest.mark.parametrize("entry", MATRIX, ids=lambda r: f"{r.method} {r.path}")
async def test_every_route_rejects_requests_without_a_token(world: World, entry: Route) -> None:
    url, body = await prepare(world, entry)

    response = await world.client.request(entry.method, url, json=body)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_the_matrix_covers_every_route_under_api_v1(world: World) -> None:
    # The OpenAPI schema is the authoritative route list (included routers are not flattened).
    paths = world.app.openapi()["paths"]
    routes = {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/api/v1")
        for method in operations
    }

    covered = {(r.method, r.path) for r in MATRIX}

    assert routes - covered == set(), "add these routes to the permission matrix"
    assert covered - routes == set(), "the matrix has routes that no longer exist"
