import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import Role, text_enum


class TicketComment(Base):
    __tablename__ = "ticket_comments"
    __table_args__ = (
        CheckConstraint("length(body) BETWEEN 1 AND 10000", name="body_length"),
        CheckConstraint(
            "NOT (is_internal AND author_role = 'customer')", name="customers_cannot_write_internal"
        ),
        Index("ix_ticket_comments_ticket_id_created_at", "ticket_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id"))
    author_role: Mapped[Role] = mapped_column(text_enum(Role, "author_role"))  # role at write time
    body: Mapped[str] = mapped_column(Text)
    is_internal: Mapped[bool] = mapped_column(server_default=text("false"), default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
