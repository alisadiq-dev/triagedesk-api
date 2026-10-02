import enum

from sqlalchemy import Enum

# Fixed width so adding a longer enum value later does not need a column change.
ENUM_COLUMN_LENGTH = 32


class Role(enum.StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"
    ADMIN = "admin"


class TicketStatus(enum.StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_ON_CUSTOMER = "waiting_on_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Priority(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Sentiment(enum.StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class AiStatus(enum.StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class CategorySource(enum.StrEnum):
    AI = "ai"
    HUMAN = "human"


class PrioritySource(enum.StrEnum):
    DEFAULT = "default"
    AI = "ai"
    KEYWORD = "keyword"
    HUMAN = "human"


class EventType(enum.StrEnum):
    TICKET_CREATED = "ticket_created"
    STATUS_CHANGED = "status_changed"
    ASSIGNED = "assigned"
    RELEASED = "released"
    CATEGORY_CHANGED = "category_changed"
    PRIORITY_CHANGED = "priority_changed"
    TRIAGE_COMPLETED = "triage_completed"
    TRIAGE_FAILED = "triage_failed"
    RESOLVED_AT_CLEARED = "resolved_at_cleared"


def text_enum[E: enum.StrEnum](enum_class: type[E], name: str) -> Enum:
    """Store a StrEnum as VARCHAR plus a CHECK constraint (not a native Postgres enum)."""
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=ENUM_COLUMN_LENGTH,
        values_callable=lambda members: [member.value for member in members],
    )
