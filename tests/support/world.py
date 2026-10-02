"""A ready-made API world for integration tests: users in every role, seeded config, a client."""

import uuid
from dataclasses import dataclass

import httpx2
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, AuthenticationError

ROLES = {
    "customer": "customer",
    "customer2": "customer",
    "agent": "agent",
    "agent2": "agent",
    "admin": "admin",
}
IDS = {name: uuid.uuid4() for name in ROLES}
TOKENS = {f"{name}-token": name for name in ROLES}


class NameTokenVerifier:
    """Token 'agent-token' is the user 'agent'. Anything else is rejected."""

    async def verify(self, token: str) -> AuthenticatedUser:
        name = TOKENS.get(token)
        if name is None:
            raise AuthenticationError
        return AuthenticatedUser(id=IDS[name], email=f"{name}@example.com")


@dataclass
class World:
    client: httpx2.AsyncClient
    session: AsyncSession
    app: FastAPI

    @property
    def ids(self) -> dict[str, uuid.UUID]:
        return IDS

    def auth(self, name: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {name}-token"}
