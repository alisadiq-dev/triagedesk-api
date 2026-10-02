import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import Role


class Me(BaseModel):
    id: uuid.UUID
    email: str | None
    role: Role


class User(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    role: Role
    created_at: datetime
