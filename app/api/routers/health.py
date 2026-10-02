from fastapi import APIRouter, Request
from sqlalchemy import text

from app.api.deps import get_database
from app.core.errors import AppError

router = APIRouter(tags=["health"])


class NotReady(AppError):
    status_code = 503
    code = "not_ready"


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up. Does not touch the database."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict[str, str]:
    """Readiness: the database is configured and answers a trivial query."""
    try:
        database = await get_database(request)
        async with database.session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise NotReady("Database is not reachable") from exc
    return {"status": "ready"}
