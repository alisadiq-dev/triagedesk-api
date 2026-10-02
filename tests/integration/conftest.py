import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

# Throwaway local container from docker-compose.yml (not a real credential).
DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/triagedesk_test"


def alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    return config


async def run_migration(database_url: str, direction: str, revision: str) -> None:
    """Alembic's async env calls asyncio.run, so it must run outside the test's event loop."""
    action = command.upgrade if direction == "up" else command.downgrade
    await asyncio.to_thread(action, alembic_config(database_url), revision)


@pytest.fixture
def test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest.fixture
def settings(test_database_url: str) -> Settings:
    return Settings(_env_file=None, database_url=SecretStr(test_database_url))


@pytest.fixture
async def fresh_database_url(test_database_url: str) -> AsyncIterator[str]:
    """A brand-new empty database, created for one test and dropped afterwards."""
    name = f"t_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(test_database_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(test_database_url).set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


@pytest.fixture
async def engine(fresh_database_url: str) -> AsyncIterator[AsyncEngine]:
    """Engine on a fresh database migrated to head."""
    await run_migration(fresh_database_url, "up", "head")
    engine = create_async_engine(fresh_database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
