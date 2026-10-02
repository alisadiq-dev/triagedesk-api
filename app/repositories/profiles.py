import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Profile


class ProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_absent(self, profile_id: uuid.UUID, email: str | None) -> None:
        """Safe under concurrency: a second insert for the same id is silently skipped."""
        statement = insert(Profile).values(id=profile_id, email=email).on_conflict_do_nothing()
        await self._session.execute(statement)

    async def get(self, profile_id: uuid.UUID) -> Profile | None:
        return await self._session.get(Profile, profile_id, populate_existing=True)
