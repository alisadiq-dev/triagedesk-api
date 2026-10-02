from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx2
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.security import AuthenticatedUser
from app.main import create_app
from app.models import Ticket, TicketComment
from tests.support.factories import make_ticket
from tests.support.world import NameTokenVerifier, World

TICKETS = "/api/v1/tickets"

ClientFactory = Callable[
    ..., AbstractAsyncContextManager[tuple[httpx2.AsyncClient, "CountingVerifier"]]
]


class CountingVerifier(NameTokenVerifier):
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, token: str) -> AuthenticatedUser:
        self.calls += 1
        return await super().verify(token)


@pytest.fixture
def make_client(fresh_database_url: str, world: World) -> ClientFactory:
    """An app with small rate limits on the same database as the `world` fixture."""

    @asynccontextmanager
    async def build(**limits: object) -> AsyncIterator[tuple[httpx2.AsyncClient, CountingVerifier]]:
        base = Settings(
            _env_file=None,
            database_url=SecretStr(fresh_database_url),
            ai_recovery_enabled=False,
            rate_limit_public_per_minute=3,
            rate_limit_api_per_minute=1000,
            rate_limit_ticket_create_per_minute=2,
            rate_limit_comment_per_minute=2,
        )
        verifier = CountingVerifier()
        app = create_app(base.model_copy(update=limits), token_verifier=verifier)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, verifier
        if app.state.database is not None:
            await app.state.database.dispose()

    return build


def auth(world: World, who: str) -> dict[str, str]:
    return world.auth(who)


async def test_public_routes_are_limited_per_client_with_the_shared_error_format(
    make_client: ClientFactory,
) -> None:
    async with make_client() as (client, _):
        allowed = [(await client.get("/health")).status_code for _ in range(3)]
        refused = await client.get("/health")

    assert allowed == [200, 200, 200]
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert int(refused.headers["Retry-After"]) >= 1


async def test_health_and_ready_share_one_public_budget(make_client: ClientFactory) -> None:
    async with make_client() as (client, _):
        await client.get("/health")
        await client.get("/health")
        await client.get("/ready")
        refused = await client.get("/ready")

    assert refused.status_code == 429


async def test_the_api_limit_applies_before_the_token_is_even_checked(
    make_client: ClientFactory,
) -> None:
    async with make_client(rate_limit_api_per_minute=3) as (client, verifier):
        bad = {"Authorization": "Bearer nonsense"}
        first = [(await client.get("/api/v1/me", headers=bad)).status_code for _ in range(3)]
        refused = await client.get("/api/v1/me", headers=bad)

    assert first == [401, 401, 401]
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert verifier.calls == 3


async def test_ticket_creation_is_limited_per_user_and_the_extra_ticket_is_not_created(
    make_client: ClientFactory, world: World
) -> None:
    body = {"title": "t", "description": "d"}
    async with make_client() as (client, _):
        created = [
            (await client.post(TICKETS, json=body, headers=auth(world, "customer"))).status_code
            for _ in range(2)
        ]
        refused = await client.post(TICKETS, json=body, headers=auth(world, "customer"))
        other_user = await client.post(TICKETS, json=body, headers=auth(world, "customer2"))

    count = await world.session.scalar(
        select(func.count()).select_from(Ticket).where(Ticket.customer_id == world.ids["customer"])
    )
    assert created == [201, 201]
    assert refused.status_code == 429
    assert "Retry-After" in refused.headers
    assert other_user.status_code == 201
    assert count == 2


async def test_invalid_ticket_requests_also_count_towards_the_limit(
    make_client: ClientFactory, world: World
) -> None:
    async with make_client() as (client, _):
        invalid = [
            (await client.post(TICKETS, json={}, headers=auth(world, "customer"))).status_code
            for _ in range(2)
        ]
        refused = await client.post(
            TICKETS, json={"title": "t", "description": "d"}, headers=auth(world, "customer")
        )

    assert invalid == [422, 422]
    assert refused.status_code == 429


async def test_comment_creation_is_limited_per_user(
    make_client: ClientFactory, world: World
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])
    url = f"{TICKETS}/{ticket.id}/comments"
    async with make_client() as (client, _):
        sent = [
            (
                await client.post(url, json={"body": "hi"}, headers=auth(world, "customer"))
            ).status_code
            for _ in range(2)
        ]
        refused = await client.post(url, json={"body": "hi"}, headers=auth(world, "customer"))

    count = await world.session.scalar(select(func.count()).select_from(TicketComment))
    assert sent == [201, 201]
    assert refused.status_code == 429
    assert count == 2


async def test_reading_is_not_limited_by_the_create_limits(
    make_client: ClientFactory, world: World
) -> None:
    async with make_client() as (client, _):
        statuses = [
            (await client.get(TICKETS, headers=auth(world, "customer"))).status_code
            for _ in range(10)
        ]

    assert statuses == [200] * 10


async def test_the_limits_can_be_switched_off(make_client: ClientFactory) -> None:
    async with make_client(rate_limit_enabled=False) as (client, _):
        statuses = {(await client.get("/health")).status_code for _ in range(20)}

    assert statuses == {200}


async def test_a_refused_request_never_reaches_authentication_failures_in_the_error_body(
    make_client: ClientFactory,
) -> None:
    async with make_client(rate_limit_api_per_minute=1) as (client, _):
        await client.get("/api/v1/me")
        refused = await client.get("/api/v1/me", headers={"Authorization": "Bearer secret-token"})

    assert refused.status_code == 429
    assert "secret-token" not in refused.text
    assert "WWW-Authenticate" not in refused.headers


# --- coverage of the whole /api/v1 surface (audit M1) ----------------------------------------


async def test_unknown_paths_and_wrong_methods_under_api_v1_are_limited_too(
    make_client: ClientFactory,
) -> None:
    async with make_client(rate_limit_api_per_minute=3) as (client, _):
        unknown = [(await client.get("/api/v1/nope")).status_code for _ in range(3)]
        refused_unknown = await client.get("/api/v1/nope")
    async with make_client(rate_limit_api_per_minute=3) as (client, _):
        wrong_method = [(await client.delete(TICKETS)).status_code for _ in range(3)]
        refused_method = await client.delete(TICKETS)

    assert unknown == [404, 404, 404]
    assert wrong_method == [405, 405, 405]
    assert refused_unknown.status_code == refused_method.status_code == 429


async def test_requests_with_a_bad_body_and_no_token_are_limited_too(
    make_client: ClientFactory,
) -> None:
    async with make_client(rate_limit_api_per_minute=3) as (client, verifier):
        sent = [(await client.post(TICKETS, content=b"{not json")).status_code for _ in range(3)]
        refused = await client.post(TICKETS, content=b"{not json")

    assert sent == [401, 401, 401]  # no token: refused before the body is looked at
    assert refused.status_code == 429
    assert verifier.calls == 0


async def test_a_refused_request_still_carries_a_request_id_and_the_security_headers(
    make_client: ClientFactory,
) -> None:
    async with make_client(rate_limit_api_per_minute=1) as (client, _):
        await client.get("/api/v1/me")
        refused = await client.get("/api/v1/me", headers={"X-Request-ID": "abc-123"})

    assert refused.status_code == 429
    assert refused.headers["X-Request-ID"] == "abc-123"
    assert refused.headers["X-Content-Type-Options"] == "nosniff"


async def test_a_ipv6_client_is_limited_per_slash_64_not_per_address(
    fresh_database_url: str,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(fresh_database_url),
        ai_recovery_enabled=False,
        rate_limit_public_per_minute=2,
    )
    app = create_app(settings)
    addresses = ["2001:db8:1:2::1", "2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:3::1"]
    results = []
    for address in addresses:
        transport = httpx2.ASGITransport(app=app, client=(address, 1234))
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            results.append([(await client.get("/health")).status_code for _ in range(2)])
    transport = httpx2.ASGITransport(app=app, client=("2001:db8:1:2::99", 1))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        same_64 = await client.get("/health")

    assert results[0] == [200, 200]
    assert results[1] == [429, 429]  # same /64 as the first address: shared budget
    assert results[2] == [200, 200]  # a different /64
    assert same_64.status_code == 429
