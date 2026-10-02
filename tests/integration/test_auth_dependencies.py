import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import httpx2
import pytest
from fastapi import Depends, FastAPI
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_actor, require_roles
from app.core.config import Settings
from app.core.security import AuthenticatedUser, AuthenticationError
from app.main import create_app
from app.models import Profile
from app.models.enums import Role
from app.services.permissions import Actor

CUSTOMER_ID, AGENT_ID, ADMIN_ID, NEW_USER_ID = (uuid.uuid4() for _ in range(4))
TOKENS = {
    "customer-token": AuthenticatedUser(CUSTOMER_ID, "c@example.com"),
    "agent-token": AuthenticatedUser(AGENT_ID, "a@example.com"),
    "admin-token": AuthenticatedUser(ADMIN_ID, "admin@example.com"),
    "new-user-token": AuthenticatedUser(NEW_USER_ID, "new@example.com"),
}


class FakeVerifier:
    async def verify(self, token: str) -> AuthenticatedUser:
        if token not in TOKENS:
            raise AuthenticationError("Invalid token")
        return TOKENS[token]


def build_app(database_url: str) -> FastAPI:
    settings = Settings(_env_file=None, database_url=SecretStr(database_url))
    app = create_app(settings, token_verifier=FakeVerifier())

    @app.get("/test/whoami")
    async def whoami(actor: Annotated[Actor, Depends(get_actor)]) -> dict[str, str]:
        return {"id": str(actor.id), "role": actor.role}

    @app.get("/test/staff")
    async def staff(
        actor: Annotated[Actor, Depends(require_roles(Role.AGENT, Role.ADMIN))],
    ) -> dict[str, str]:
        return {"role": actor.role}

    @app.get("/test/admin")
    async def admin(actor: Annotated[Actor, Depends(require_roles(Role.ADMIN))]) -> dict[str, str]:
        return {"role": actor.role}

    return app


@pytest.fixture
async def client(
    fresh_database_url: str, session: AsyncSession
) -> AsyncIterator[httpx2.AsyncClient]:
    session.add_all(
        [
            Profile(id=CUSTOMER_ID, role=Role.CUSTOMER),
            Profile(id=AGENT_ID, role=Role.AGENT),
            Profile(id=ADMIN_ID, role=Role.ADMIN),
        ]
    )
    await session.commit()
    app = build_app(fresh_database_url)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        yield http_client
    if app.state.database is not None:
        await app.state.database.dispose()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_request_without_a_token_is_401(client: httpx2.AsyncClient) -> None:
    response = await client.get("/test/whoami")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Basic abc", "Bearer", "Bearer not-a-known-token"])
async def test_malformed_or_unknown_credentials_are_401(
    client: httpx2.AsyncClient, header: str
) -> None:
    response = await client.get("/test/whoami", headers={"Authorization": header})

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("token", "expected_role"),
    [("customer-token", "customer"), ("agent-token", "agent"), ("admin-token", "admin")],
)
async def test_actor_role_comes_from_the_database(
    client: httpx2.AsyncClient, token: str, expected_role: str
) -> None:
    response = await client.get("/test/whoami", headers=bearer(token))

    assert response.status_code == 200
    assert response.json()["role"] == expected_role


@pytest.mark.parametrize(
    ("path", "token", "status"),
    [
        ("/test/staff", "customer-token", 403),
        ("/test/staff", "agent-token", 200),
        ("/test/staff", "admin-token", 200),
        ("/test/admin", "customer-token", 403),
        ("/test/admin", "agent-token", 403),
        ("/test/admin", "admin-token", 200),
    ],
)
async def test_role_gates_allow_only_the_listed_roles(
    client: httpx2.AsyncClient, path: str, token: str, status: int
) -> None:
    response = await client.get(path, headers=bearer(token))

    assert response.status_code == status
    if status == 403:
        assert response.json()["error"]["code"] == "forbidden"


async def test_first_request_from_an_unknown_user_creates_a_customer_profile(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    response = await client.get("/test/whoami", headers=bearer("new-user-token"))

    assert response.json() == {"id": str(NEW_USER_ID), "role": "customer"}
    created = await session.scalar(select(Profile).where(Profile.id == NEW_USER_ID))
    assert created is not None
    assert created.email == "new@example.com"
