from fastapi import FastAPI

from app.api.routers import health
from app.core.errors import register_error_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="TriageDesk API")
    register_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
