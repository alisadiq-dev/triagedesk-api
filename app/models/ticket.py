import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    and_,
    func,
    or_,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from app.models.base import Base
from app.models.enums import (
    AiStatus,
    CategorySource,
    Priority,
    PrioritySource,
    Sentiment,
    TicketStatus,
    text_enum,
)

SEARCH_VECTOR_SQL = (
    "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))"
)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("length(description) BETWEEN 1 AND 10000", name="description_length"),
        CheckConstraint(
            "(status IN ('resolved', 'closed')) = (resolved_at IS NOT NULL)",
            name="resolved_at_matches_status",
        ),
        Index("ix_tickets_status", "status"),
        Index("ix_tickets_assignee_id", "assignee_id"),
        Index("ix_tickets_created_at", "created_at"),
        Index("ix_tickets_customer_id_created_at", "customer_id", "created_at"),
        Index(
            "ix_tickets_first_response_due_at_unanswered",
            "first_response_due_at",
            postgresql_where=text("first_responded_at IS NULL"),
        ),
        Index(
            "ix_tickets_resolution_due_at_unresolved",
            "resolution_due_at",
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index("ix_tickets_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id"))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    category_source: Mapped[CategorySource | None] = mapped_column(
        text_enum(CategorySource, "category_source")
    )
    priority: Mapped[Priority] = mapped_column(
        text_enum(Priority, "priority"), server_default=text("'medium'"), default=Priority.MEDIUM
    )
    priority_source: Mapped[PrioritySource] = mapped_column(
        text_enum(PrioritySource, "priority_source"),
        server_default=text("'default'"),
        default=PrioritySource.DEFAULT,
    )
    sentiment: Mapped[Sentiment | None] = mapped_column(text_enum(Sentiment, "sentiment"))
    status: Mapped[TicketStatus] = mapped_column(
        text_enum(TicketStatus, "status"),
        server_default=text("'open'"),
        default=TicketStatus.OPEN,
    )
    ai_status: Mapped[AiStatus] = mapped_column(
        text_enum(AiStatus, "ai_status"),
        server_default=text("'pending'"),
        default=AiStatus.PENDING,
    )
    ai_suggested_reply: Mapped[str | None] = mapped_column(Text)
    ai_model: Mapped[str | None] = mapped_column(Text)
    ai_prompt_version: Mapped[str | None] = mapped_column(Text)
    first_response_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolution_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed(SEARCH_VECTOR_SQL, persisted=True), deferred=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @classmethod
    def breached_clause(cls, now: ColumnElement[datetime] | None = None) -> ColumnElement[bool]:
        """SQL form of the breach rule, usable in WHERE clauses (filter and paginate in the DB)."""
        current = func.now() if now is None else now
        return or_(
            and_(cls.first_responded_at.is_(None), current > cls.first_response_due_at),
            and_(cls.resolved_at.is_(None), current > cls.resolution_due_at),
        )

    def first_response_breached_at(self, now: datetime) -> bool:
        return self.first_responded_at is None and now > self.first_response_due_at

    def resolution_breached_at(self, now: datetime) -> bool:
        return self.resolved_at is None and now > self.resolution_due_at

    def is_breached_at(self, now: datetime) -> bool:
        """Python form of the same rule. Keep in sync with breached_clause (tested together)."""
        return self.first_response_breached_at(now) or self.resolution_breached_at(now)

    @property
    def is_breached(self) -> bool:
        return self.is_breached_at(datetime.now(UTC))
