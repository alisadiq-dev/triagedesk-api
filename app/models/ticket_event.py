import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import EventType, text_enum


class TicketEvent(Base):
    """Audit log: who did what to a ticket, from which value to which value, and when."""

    __tablename__ = "ticket_events"
    __table_args__ = (Index("ix_ticket_events_ticket_id_created_at", "ticket_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id")
    )  # null = system/AI
    event_type: Mapped[EventType] = mapped_column(text_enum(EventType, "event_type"))
    from_value: Mapped[str | None] = mapped_column(Text)
    to_value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
