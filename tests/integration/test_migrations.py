from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.models import Base
from tests.integration.conftest import run_migration


async def table_names(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            names = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
    finally:
        await engine.dispose()
    return names - {"alembic_version"}


async def test_upgrade_head_creates_all_tables(fresh_database_url: str) -> None:
    await run_migration(fresh_database_url, "up", "head")

    assert await table_names(fresh_database_url) == {
        "profiles",
        "categories",
        "sla_policies",
        "tickets",
        "ticket_comments",
        "ticket_events",
    }


async def test_downgrade_to_base_removes_everything_and_upgrade_works_again(
    fresh_database_url: str,
) -> None:
    await run_migration(fresh_database_url, "up", "head")
    await run_migration(fresh_database_url, "down", "base")
    assert await table_names(fresh_database_url) == set()

    await run_migration(fresh_database_url, "up", "head")

    assert len(await table_names(fresh_database_url)) == 6


async def test_migrated_schema_matches_the_models(fresh_database_url: str) -> None:
    await run_migration(fresh_database_url, "up", "head")
    engine = create_async_engine(fresh_database_url)
    try:
        async with engine.connect() as connection:
            diff = await connection.run_sync(
                lambda c: compare_metadata(
                    MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata
                )
            )
    finally:
        await engine.dispose()

    assert diff == []
