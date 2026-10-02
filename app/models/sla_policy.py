import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import Priority, text_enum


class SlaPolicy(Base):
    __tablename__ = "sla_policies"
    __table_args__ = (
        CheckConstraint("response_hours > 0", name="response_hours_positive"),
        CheckConstraint("resolution_hours >= response_hours", name="resolution_after_response"),
    )

    priority: Mapped[Priority] = mapped_column(text_enum(Priority, "priority"), primary_key=True)
    response_hours: Mapped[int]
    resolution_hours: Mapped[int]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id"))
