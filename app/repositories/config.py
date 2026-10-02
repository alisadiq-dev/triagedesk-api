from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, SlaPolicy
from app.models.enums import Priority

_URGENCY = case(
    {Priority.URGENT: 0, Priority.HIGH: 1, Priority.MEDIUM: 2, Priority.LOW: 3},
    value=SlaPolicy.priority,
)


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, include_inactive: bool) -> list[Category]:
        query = select(Category).order_by(Category.name)
        if not include_inactive:
            query = query.where(Category.is_active.is_(True))
        return list(await self._session.scalars(query))

    async def get(self, category_id: int) -> Category | None:
        return await self._session.get(Category, category_id, populate_existing=True)

    def add(self, category: Category) -> None:
        self._session.add(category)


class SlaPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self) -> list[SlaPolicy]:
        rows = await self._session.scalars(select(SlaPolicy).order_by(_URGENCY))
        return list(rows)

    async def get(self, priority: Priority) -> SlaPolicy | None:
        return await self._session.get(SlaPolicy, priority, populate_existing=True)
