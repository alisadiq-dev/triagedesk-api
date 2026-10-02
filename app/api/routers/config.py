from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_actor, get_session
from app.models.enums import Priority
from app.schemas.config import (
    Category,
    CategoryCreate,
    CategoryUpdate,
    SlaPolicy,
    SlaPolicyUpdate,
)
from app.services.categories import CategoryService
from app.services.permissions import Actor
from app.services.sla import SlaPolicyService

categories = APIRouter(prefix="/categories", tags=["categories"])
sla_policies = APIRouter(prefix="/sla-policies", tags=["sla-policies"])

ActorDep = Annotated[Actor, Depends(get_actor)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@categories.get("")
async def list_categories(
    actor: ActorDep, session: SessionDep, include_inactive: bool = False
) -> list[Category]:
    rows = await CategoryService(session, actor).list_categories(include_inactive)
    return [Category.model_validate(row) for row in rows]


@categories.post("", status_code=201)
async def create_category(body: CategoryCreate, actor: ActorDep, session: SessionDep) -> Category:
    return Category.model_validate(await CategoryService(session, actor).create(body))


@categories.patch("/{category_id}")
async def update_category(
    category_id: int, body: CategoryUpdate, actor: ActorDep, session: SessionDep
) -> Category:
    return Category.model_validate(await CategoryService(session, actor).update(category_id, body))


@sla_policies.get("")
async def list_sla_policies(actor: ActorDep, session: SessionDep) -> list[SlaPolicy]:
    rows = await SlaPolicyService(session, actor).list_policies()
    return [SlaPolicy.model_validate(row) for row in rows]


@sla_policies.patch("/{priority}")
async def update_sla_policy(
    priority: Priority, body: SlaPolicyUpdate, actor: ActorDep, session: SessionDep
) -> SlaPolicy:
    return SlaPolicy.model_validate(await SlaPolicyService(session, actor).update(priority, body))
