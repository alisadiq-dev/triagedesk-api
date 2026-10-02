from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import Database
from app.core.jwt_auth import build_verifier
from app.core.security import AuthenticatedUser, AuthenticationError, TokenVerifier
from app.models.enums import Role
from app.services.permissions import Actor, require_role
from app.services.profiles import ProfileService

bearer_scheme = HTTPBearer(auto_error=False)


async def get_database(request: Request) -> Database:
    """Create the Database lazily on first use, so the app can start without a DB URL."""
    app_state = request.app.state
    if app_state.database is None:
        app_state.database = Database(app_state.settings or get_settings())
    database: Database = app_state.database
    return database


async def get_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    async with database.session() as session:
        yield session


async def get_token_verifier(request: Request) -> TokenVerifier:
    """Build the real JWT verifier lazily (no network happens until a token needs checking)."""
    app_state = request.app.state
    if app_state.token_verifier is None:
        app_state.token_verifier = build_verifier(app_state.settings or get_settings())
    verifier: TokenVerifier = app_state.token_verifier
    return verifier


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> AuthenticatedUser:
    if credentials is None:
        raise AuthenticationError()
    return await verifier.verify(credentials.credentials)


async def get_actor(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Actor:
    """The verified user plus the role read from the database (created on first request)."""
    profile = await ProfileService(session).ensure_profile(user)
    return Actor(id=profile.id, role=profile.role)


def require_roles(*roles: Role) -> Callable[[Actor], Awaitable[Actor]]:
    """Coarse gate for routes that only some roles may call; services enforce the details."""

    async def dependency(actor: Annotated[Actor, Depends(get_actor)]) -> Actor:
        require_role(actor, *roles)
        return actor

    return dependency
