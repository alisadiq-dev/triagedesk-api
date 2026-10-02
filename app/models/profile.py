import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import Role, text_enum


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)  # the Supabase user id (JWT sub)
    email: Mapped[str | None] = mapped_column(Text)
    role: Mapped[Role] = mapped_column(
        text_enum(Role, "role"), server_default=text("'customer'"), default=Role.CUSTOMER
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
