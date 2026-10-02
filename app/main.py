from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.ai.interface import TriageModel
from app.api.routers import health
from app.api.v1 import api_v1
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import register_error_handlers
from app.core.jwt_auth import JwtTokenVerifier
from app.core.logging import RequestIdMiddleware, configure_logging
from app.core.security import TokenVerifier


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
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
    app = FastAPI(title="TriageDesk API", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = None
    app.state.token_verifier = token_verifier
    app.state.triage_model = triage_model
    app.state.triage_runner = None
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(api_v1)
    return app


configure_logging()
app = create_app()
