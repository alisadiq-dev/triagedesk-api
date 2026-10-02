from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_actor
from app.schemas.users import Me
from app.services.permissions import Actor

router = APIRouter(tags=["identity"])


@router.get("/me")
async def me(actor: Annotated[Actor, Depends(get_actor)]) -> Me:
    return Me(id=actor.id, email=actor.email, role=actor.role)
