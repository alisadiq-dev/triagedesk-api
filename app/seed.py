"""Idempotent seed: default categories, SLA policies and the bootstrap admin.

Safe to re-run. Existing rows (and any edits an admin made) are never overwritten,
except that the bootstrap profile is always promoted to admin.
"""

import asyncio
import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import Database
from app.models import Category, Profile, SlaPolicy
from app.models.enums import Priority, Role

DEFAULT_CATEGORIES = [
    "Billing",
    "Technical Issue",
    "Account Access",
    "Feature Request",
    "General Inquiry",
]

# priority -> (response_hours, resolution_hours)
DEFAULT_SLA_POLICIES = {
    Priority.URGENT: (1, 4),
    Priority.HIGH: (4, 24),
    Priority.MEDIUM: (8, 72),
    Priority.LOW: (24, 168),
}


async def seed(session: AsyncSession, bootstrap_admin_sub: uuid.UUID | None) -> None:
    await session.execute(
        insert(Category)
        .values([{"name": name} for name in DEFAULT_CATEGORIES])
        .on_conflict_do_nothing()
    )
    await session.execute(
        insert(SlaPolicy)
        .values(
            [
                {"priority": priority, "response_hours": response, "resolution_hours": resolution}
                for priority, (response, resolution) in DEFAULT_SLA_POLICIES.items()
            ]
        )
        .on_conflict_do_nothing()
    )
    if bootstrap_admin_sub is not None:
        statement = insert(Profile).values(id=bootstrap_admin_sub, role=Role.ADMIN)
        await session.execute(
            statement.on_conflict_do_update(index_elements=["id"], set_={"role": Role.ADMIN})
        )
    await session.commit()


async def main() -> None:
    settings = get_settings()
    database = Database(settings)
    try:
        async with database.session() as session:
            await seed(session, settings.bootstrap_admin_sub)
    finally:
        await database.dispose()
    print("seed complete")


if __name__ == "__main__":
    asyncio.run(main())
