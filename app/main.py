import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.ai.interface import TriageModel
from app.ai.recovery import TriageRecovery, run_periodically
from app.api.deps import ensure_database, ensure_rate_limits, ensure_triage_runner
from app.api.routers import health
from app.api.v1 import api_v1
from app.core.config import Settings, get_hardening_settings, get_settings
from app.core.db import Database
from app.core.errors import register_error_handlers
from app.core.hardening import (
    BodyLimitMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.jwt_auth import JwtTokenVerifier
from app.core.logging import RequestIdMiddleware, configure_logging
from app.core.security import TokenVerifier


def start_recovery(app: FastAPI, settings: Settings) -> asyncio.Task[None] | None:
    """Start the stuck-pending sweeper (ADR 0006) unless it is switched off."""
    if not settings.ai_recovery_enabled:
        return None
    recovery = TriageRecovery(
        ensure_database(app),
        ensure_triage_runner(app),
        settings.ai_recovery_age_seconds,
        settings.ai_recovery_batch_size,
        settings.ai_recovery_max_attempts,
    )
    return asyncio.create_task(
        run_periodically(recovery.sweep, settings.ai_recovery_interval_seconds)
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.recovery_task = start_recovery(app, app.state.settings or get_settings())
    yield
    task: asyncio.Task[None] | None = app.state.recovery_task
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    database: Database | None = app.state.database
    if database is not None:
        await database.dispose()
    verifier = app.state.token_verifier
    if isinstance(verifier, JwtTokenVerifier):
        await verifier.aclose()


def create_app(
    settings: Settings | None = None,
    token_verifier: TokenVerifier | None = None,
    triage_model: TriageModel | None = None,
) -> FastAPI:
    hardening = settings or get_hardening_settings()
    docs = hardening.api_docs_enabled
    app = FastAPI(
        title="TriageDesk API",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.database = None
    app.state.token_verifier = token_verifier
    app.state.triage_model = triage_model
    app.state.triage_runner = None
    app.state.recovery_task = None
    app.state.rate_limits = None
    app.add_middleware(RateLimitMiddleware, get_limits=lambda: ensure_rate_limits(app))
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(BodyLimitMiddleware, max_bytes=hardening.max_request_body_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(api_v1)
    return app


configure_logging()
app = create_app()
