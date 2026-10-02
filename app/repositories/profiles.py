import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Profile
from app.models.enums import Role


class ProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_absent(self, profile_id: uuid.UUID, email: str | None) -> None:
        """Safe under concurrency: a second insert for the same id is silently skipped."""
        statement = insert(Profile).values(id=profile_id, email=email).on_conflict_do_nothing()
        await self._session.execute(statement)

    async def get(self, profile_id: uuid.UUID) -> Profile | None:
        return await self._session.get(Profile, profile_id, populate_existing=True)

    async def list_page(
        self, role: Role | None, offset: int, limit: int
    ) -> tuple[list[Profile], int]:
        base = select(Profile)
        if role is not None:
            base = base.where(Profile.role == role)
        total = await self._session.scalar(select(func.count()).select_from(base.subquery()))
        rows = await self._session.scalars(
            base.order_by(Profile.created_at, Profile.id).offset(offset).limit(limit)
        )
        return list(rows), total or 0

    async def get_locked(self, profile_id: uuid.UUID, *, exclusive: bool) -> Profile | None:
        """Read the profile and hold a row lock until the transaction ends.

        FOR SHARE (exclusive=False) lets claim and assign rely on the role they just read;
        FOR UPDATE (exclusive=True) lets a role change wait for them to finish.
        """
        query = (
            select(Profile)
            .where(Profile.id == profile_id)
            .with_for_update(read=not exclusive)
            .execution_options(populate_existing=True)
        )
        return (await self._session.scalars(query)).one_or_none()

    async def lock_admins(self) -> list[uuid.UUID]:
        """Lock every admin row in a fixed order (no deadlocks) and return their ids."""
        rows = await self._session.scalars(
            select(Profile.id)
            .where(Profile.role == Role.ADMIN)
            .order_by(Profile.id)
            .with_for_update()
        )
        return list(rows)
