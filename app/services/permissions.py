import uuid
from dataclasses import dataclass

from app.core.errors import AppError
from app.models.enums import Role


class ForbiddenError(AppError):
    """403: authenticated, but the role is not allowed to do this."""

    status_code = 403
    code = "forbidden"


@dataclass(frozen=True)
class Actor:
    """Who is acting, with the role read from the database (never from the token)."""

    id: uuid.UUID
    role: Role


def require_role(actor: Actor, *allowed: Role) -> None:
    if actor.role not in allowed:
        raise ForbiddenError("You do not have permission to do this")
