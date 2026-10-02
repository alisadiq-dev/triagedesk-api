import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_actor, get_session
from app.models.enums import Role
from app.schemas.common import Page, PageParamsDep
from app.schemas.users import User
from app.services.permissions import Actor
from app.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"])


class RoleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role


def get_user_service(
    actor: Annotated[Actor, Depends(get_actor)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserService:
    return UserService(session, actor)


Service = Annotated[UserService, Depends(get_user_service)]


@router.get("")
async def list_users(service: Service, page: PageParamsDep, role: Role | None = None) -> Page[User]:
    users, total = await service.list_users(role, page)
    return Page[User](
        items=[User.model_validate(user) for user in users],
        page=page.page,
        page_size=page.page_size,
        total=total,
    )


@router.get("/{user_id}")
async def get_user(user_id: uuid.UUID, service: Service) -> User:
    return User.model_validate(await service.get_user(user_id))


@router.patch("/{user_id}")
async def change_role(user_id: uuid.UUID, body: RoleChange, service: Service) -> User:
    return User.model_validate(await service.change_role(user_id, body.role))
