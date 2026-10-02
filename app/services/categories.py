from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category
from app.models.enums import Role
from app.repositories.config import CategoryRepository
from app.schemas.config import CategoryCreate, CategoryUpdate
from app.services.errors import NameTakenError, NotFoundError
from app.services.permissions import Actor, require_role


class CategoryService:
    def __init__(self, session: AsyncSession, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._categories = CategoryRepository(session)

    async def list_categories(self, include_inactive: bool) -> list[Category]:
        require_role(self._actor, Role.AGENT, Role.ADMIN)
        # Only admins may see deactivated categories.
        return await self._categories.list(include_inactive and self._actor.role == Role.ADMIN)

    async def create(self, data: CategoryCreate) -> Category:
        require_role(self._actor, Role.ADMIN)
        category = Category(name=data.name, description=data.description)
        self._categories.add(category)
        await self._commit_unique(category)
        return category

    async def update(self, category_id: int, data: CategoryUpdate) -> Category:
        require_role(self._actor, Role.ADMIN)
        category = await self._categories.get(category_id)
        if category is None:
            raise NotFoundError("Category not found")
        for field in data.model_fields_set:
            setattr(category, field, getattr(data, field))
        await self._commit_unique(category)
        return category

    async def _commit_unique(self, category: Category) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise NameTakenError from exc
