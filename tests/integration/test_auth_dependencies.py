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
from app.core.jwt_auth import JwtTokenVerifier
from app.core.security import AuthenticatedUser, AuthenticationError, AuthServiceUnavailableError
from app.main import create_app
from app.models import Profile
from app.models.enums import Role
from app.services.permissions import Actor
from tests.support import jwt_helpers
from tests.support.jwt_helpers import StaticKeys

SIGNING_KEY = jwt_helpers.new_key()
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
            raise AuthenticationError()
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


class CountingVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, token: str) -> AuthenticatedUser:
        self.calls += 1
        raise AuthenticationError


async def test_missing_token_gets_401_before_any_key_or_database_work() -> None:
    verifier = CountingVerifier()
    unreachable = Settings(
        _env_file=None, database_url=SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/none")
    )
    app = create_app(unreachable, token_verifier=verifier)

    @app.get("/test/whoami")
    async def whoami(actor: Annotated[Actor, Depends(get_actor)]) -> dict[str, str]:
        return {"id": str(actor.id)}

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        response = await http_client.get("/test/whoami")

    assert response.status_code == 401
    assert verifier.calls == 0
    assert app.state.database is None


async def test_missing_and_invalid_tokens_get_the_identical_401_body(
    client: httpx2.AsyncClient,
) -> None:
    missing = await client.get("/test/whoami")
    invalid = await client.get("/test/whoami", headers=bearer("not-a-known-token"))

    assert missing.status_code == invalid.status_code == 401
    assert missing.json() == invalid.json()


async def test_an_unavailable_key_service_is_a_503_in_the_error_format() -> None:
    class Unavailable:
        async def verify(self, token: str) -> AuthenticatedUser:
            raise AuthServiceUnavailableError

    app = create_app(token_verifier=Unavailable())

    @app.get("/test/whoami")
    async def whoami(actor: Annotated[Actor, Depends(get_actor)]) -> dict[str, str]:
        return {"id": str(actor.id)}

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        response = await http_client.get("/test/whoami", headers=bearer("anything"))

    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "auth_unavailable", "message": "Authentication service unavailable"}
    }


async def test_without_an_injected_verifier_the_real_one_is_built_from_settings(
    fresh_database_url: str,
) -> None:
    settings = Settings(_env_file=None, database_url=SecretStr(fresh_database_url))
    app = create_app(settings)

    @app.get("/test/whoami")
    async def whoami(actor: Annotated[Actor, Depends(get_actor)]) -> dict[str, str]:
        return {"id": str(actor.id)}

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        response = await http_client.get("/test/whoami", headers=bearer("not.a.jwt"))

    assert response.status_code == 401
    assert isinstance(app.state.token_verifier, JwtTokenVerifier)
    await app.state.token_verifier.aclose()


@pytest.fixture
async def real_client(
    fresh_database_url: str, session: AsyncSession
) -> AsyncIterator[httpx2.AsyncClient]:
    """The app wired with the real JwtTokenVerifier and a local test key (no mocks of the logic)."""
    session.add(Profile(id=uuid.UUID(jwt_helpers.USER_ID), role=Role.CUSTOMER))
    await session.commit()
    settings = Settings(_env_file=None, database_url=SecretStr(fresh_database_url))
    verifier = JwtTokenVerifier(
        StaticKeys(**{jwt_helpers.KID: SIGNING_KEY.public_key()}),
        issuer=jwt_helpers.ISSUER,
        audience=jwt_helpers.AUDIENCE,
    )
    app = create_app(settings, token_verifier=verifier)

    @app.get("/test/whoami")
    async def whoami(actor: Annotated[Actor, Depends(get_actor)]) -> dict[str, str]:
        return {"id": str(actor.id), "role": actor.role}

    @app.get("/test/admin")
    async def admin(actor: Annotated[Actor, Depends(require_roles(Role.ADMIN))]) -> dict[str, str]:
        return {"role": actor.role}

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        yield http_client
    if app.state.database is not None:
        await app.state.database.dispose()


async def test_a_role_claim_in_the_token_never_grants_privileges(
    real_client: httpx2.AsyncClient,
) -> None:
    token = jwt_helpers.sign(
        SIGNING_KEY, jwt_helpers.claims(role="admin", app_metadata={"role": "admin"})
    )

    whoami = await real_client.get("/test/whoami", headers=bearer(token))
    admin_only = await real_client.get("/test/admin", headers=bearer(token))

    assert whoami.json()["role"] == "customer"
    assert admin_only.status_code == 403


async def test_a_real_signed_token_for_a_new_user_creates_a_customer_profile(
    real_client: httpx2.AsyncClient,
) -> None:
    new_id = str(uuid.uuid4())
    token = jwt_helpers.sign(SIGNING_KEY, jwt_helpers.claims(sub=new_id))

    response = await real_client.get("/test/whoami", headers=bearer(token))

    assert response.json() == {"id": new_id, "role": "customer"}


async def test_every_kind_of_bad_token_gets_a_byte_identical_401_over_http(
    real_client: httpx2.AsyncClient,
) -> None:
    good = jwt_helpers.sign(SIGNING_KEY, jwt_helpers.claims())
    tampered = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
    bad_headers = [
        {},
        bearer(jwt_helpers.sign(SIGNING_KEY, jwt_helpers.claims(exp=1))),
        bearer(jwt_helpers.sign(SIGNING_KEY, jwt_helpers.claims(aud="other"))),
        bearer(jwt_helpers.sign(SIGNING_KEY, jwt_helpers.claims(iss="http://evil.example"))),
        bearer(jwt_helpers.sign(SIGNING_KEY, jwt_helpers.claims(is_anonymous=True))),
        bearer(jwt_helpers.sign(jwt_helpers.new_key(), jwt_helpers.claims())),
        bearer(tampered),
        bearer("not.a.jwt"),
        {"Authorization": "Basic abc"},
    ]

    responses = [await real_client.get("/test/whoami", headers=h) for h in bad_headers]

    fingerprints = {
        (r.status_code, r.content, r.headers.get("www-authenticate")) for r in responses
    }
    assert fingerprints == {(401, responses[0].content, "Bearer")}
