import enum
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models import TicketComment
from app.models.enums import Role

Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]


class AuthorType(enum.StrEnum):
    CUSTOMER = "customer"
    SUPPORT = "support"


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: Body
    is_internal: bool = False


class CustomerComment(BaseModel):
    """Allowlist: customers never learn who the support author is."""

    id: uuid.UUID
    author_type: AuthorType
    body: str
    created_at: datetime

    @classmethod
    def from_comment(cls, comment: TicketComment) -> "CustomerComment":
        author_type = (
            AuthorType.CUSTOMER if comment.author_role == Role.CUSTOMER else AuthorType.SUPPORT
        )
        return cls(
            id=comment.id, author_type=author_type, body=comment.body, created_at=comment.created_at
        )


class StaffComment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    author_id: uuid.UUID
    author_role: Role
    is_internal: bool
    body: str
    created_at: datetime
