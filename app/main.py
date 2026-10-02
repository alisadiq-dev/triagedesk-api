from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import health
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import register_error_handlers


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    database: Database | None = app.state.database
    if database is not None:
        await database.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="TriageDesk API", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = None
    register_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
