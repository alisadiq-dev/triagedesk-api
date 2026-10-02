from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gemini import build_triage_model
from app.ai.triage import TriageRunner
from app.core.config import HardeningSettings, get_hardening_settings, get_settings
from app.core.db import Database
from app.core.errors import RateLimitedError
from app.core.jwt_auth import build_verifier
from app.core.rate_limit import RateLimiter, RateLimits
from app.core.security import AuthenticatedUser, AuthenticationError, TokenVerifier
from app.models.enums import Role
from app.services.permissions import Actor, require_role
from app.services.profiles import ProfileService

bearer_scheme = HTTPBearer(auto_error=False)


def ensure_database(app: FastAPI) -> Database:
    """Create the Database lazily on first use, so the app can start without a DB URL."""
    if app.state.database is None:
        app.state.database = Database(app.state.settings or get_settings())
    database: Database = app.state.database
    return database


def ensure_triage_runner(app: FastAPI) -> TriageRunner:
    """Built lazily. Without a Gemini key, triage always takes the keyword fallback."""
    if app.state.triage_runner is None:
        settings = app.state.settings or get_settings()
        model = app.state.triage_model or build_triage_model(settings)
        app.state.triage_runner = TriageRunner(
            ensure_database(app), model, settings.ai_timeout_seconds
        )
    runner: TriageRunner = app.state.triage_runner
    return runner


def ensure_rate_limits(app: FastAPI) -> RateLimits:
    """Built lazily from the settings, like the database."""
    if app.state.rate_limits is None:
        settings: HardeningSettings = app.state.settings or get_hardening_settings()

        def per_minute(limit: int) -> RateLimiter:
            return RateLimiter(limit, 60.0)

        app.state.rate_limits = RateLimits(
            enabled=settings.rate_limit_enabled,
            public=per_minute(settings.rate_limit_public_per_minute),
            api=per_minute(settings.rate_limit_api_per_minute),
            ticket_create=per_minute(settings.rate_limit_ticket_create_per_minute),
            comment_create=per_minute(settings.rate_limit_comment_per_minute),
        )
    limits: RateLimits = app.state.rate_limits
    return limits


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _enforce(limits: RateLimits, limiter: RateLimiter, key: str) -> None:
    if limits.enabled and (retry_after := limiter.check(key)) is not None:
        raise RateLimitedError(retry_after)


async def limit_public(request: Request) -> None:
    limits = ensure_rate_limits(request.app)
    _enforce(limits, limits.public, _client_key(request))


async def limit_api(request: Request) -> None:
    """Runs before the token is checked, so floods of bad tokens are cut off cheaply."""
    limits = ensure_rate_limits(request.app)
    _enforce(limits, limits.api, _client_key(request))


async def get_database(request: Request) -> Database:
    return ensure_database(request.app)


async def get_triage_runner(request: Request) -> TriageRunner:
    return ensure_triage_runner(request.app)


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
    return Actor(id=profile.id, role=profile.role, email=profile.email)


def require_roles(*roles: Role) -> Callable[[Actor], Awaitable[Actor]]:
    """Coarse gate for routes that only some roles may call; services enforce the details."""

    async def dependency(actor: Annotated[Actor, Depends(get_actor)]) -> Actor:
        require_role(actor, *roles)
        return actor

    return dependency


async def limit_ticket_create(
    request: Request, actor: Annotated[Actor, Depends(get_actor)]
) -> None:
    limits = ensure_rate_limits(request.app)
    _enforce(limits, limits.ticket_create, str(actor.id))


async def limit_comment_create(
    request: Request, actor: Annotated[Actor, Depends(get_actor)]
) -> None:
    limits = ensure_rate_limits(request.app)
    _enforce(limits, limits.comment_create, str(actor.id))
