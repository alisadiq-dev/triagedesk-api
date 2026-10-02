from fastapi import Request

from app.core.config import get_settings
from app.core.db import Database


async def get_database(request: Request) -> Database:
    """Create the Database lazily on first use, so the app can start without a DB URL."""
    app_state = request.app.state
    if app_state.database is None:
        app_state.database = Database(app_state.settings or get_settings())
    database: Database = app_state.database
    return database
