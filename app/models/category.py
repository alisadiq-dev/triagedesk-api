from datetime import datetime

from sqlalchemy import DateTime, Identity, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"), default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Case-insensitive unique names.
Index("uq_categories_name_lower", func.lower(Category.name), unique=True)
