import enum
import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from app.models import Ticket
from app.models.enums import (
    AiStatus,
    CategorySource,
    Priority,
    PrioritySource,
    Role,
    Sentiment,
    TicketStatus,
)
from app.services.permissions import Actor

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)
]


class TicketSort(enum.StrEnum):
    NEWEST = "-created_at"
    OLDEST = "created_at"
    FIRST_RESPONSE_DUE = "first_response_due_at"
    RESOLUTION_DUE = "resolution_due_at"


CUSTOMER_SORTS = {TicketSort.NEWEST, TicketSort.OLDEST}


class TicketCreate(BaseModel):
    """A customer sends only a title and a description (extra fields are rejected)."""

    model_config = ConfigDict(extra="forbid")

    title: Title
    description: Description


class TicketOverrides(BaseModel):
    """Human overrides of the AI-set fields. Only these two fields can change after creation."""

    model_config = ConfigDict(extra="forbid")

    category_id: int | None = None
    priority: Priority | None = None

    @model_validator(mode="after")
    def at_least_one_value(self) -> Self:
        if self.category_id is None and self.priority is None:
            raise ValueError("provide category_id or priority")
        return self


class AssigneeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee_id: uuid.UUID


class CustomerTicket(BaseModel):
    """Allowlist: a new field added to the model can never leak to customers."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    status: TicketStatus
    created_at: datetime
    updated_at: datetime


class StaffTicket(CustomerTicket):
    customer_id: uuid.UUID
    customer_email: str | None
    assignee_id: uuid.UUID | None
    category_id: int | None
    category_source: CategorySource | None
    priority: Priority
    priority_source: PrioritySource
    sentiment: Sentiment | None
    ai_status: AiStatus
    ai_suggested_reply: str | None
    ai_model: str | None
    ai_prompt_version: str | None
    first_response_due_at: datetime
    resolution_due_at: datetime
    first_responded_at: datetime | None
    resolved_at: datetime | None
    sla_breached: bool

    @classmethod
    def from_ticket(
        cls, ticket: Ticket, customer_email: str | None, *, show_draft: bool = True
    ) -> "StaffTicket":
        base = CustomerTicket.model_validate(ticket).model_dump()
        return cls(
            **base,
            customer_id=ticket.customer_id,
            customer_email=customer_email,
            assignee_id=ticket.assignee_id,
            category_id=ticket.category_id,
            category_source=ticket.category_source,
            priority=ticket.priority,
            priority_source=ticket.priority_source,
            sentiment=ticket.sentiment,
            ai_status=ticket.ai_status,
            ai_suggested_reply=ticket.ai_suggested_reply if show_draft else None,
            ai_model=ticket.ai_model,
            ai_prompt_version=ticket.ai_prompt_version,
            first_response_due_at=ticket.first_response_due_at,
            resolution_due_at=ticket.resolution_due_at,
            first_responded_at=ticket.first_responded_at,
            resolved_at=ticket.resolved_at,
            sla_breached=ticket.is_breached,
        )


def render_staff_ticket(actor: Actor, ticket: Ticket, customer_email: str | None) -> StaffTicket:
    """The staff view. The AI draft is only for the assignee and admins (PRD section 4)."""
    show_draft = actor.role == Role.ADMIN or ticket.assignee_id == actor.id
    return StaffTicket.from_ticket(ticket, customer_email, show_draft=show_draft)


def render_ticket(actor: Actor, ticket: Ticket, customer_email: str | None) -> CustomerTicket:
    """The customer view for customers, the staff view (a CustomerTicket subclass) for staff."""
    if actor.role == Role.CUSTOMER:
        return CustomerTicket.model_validate(ticket)
    return render_staff_ticket(actor, ticket, customer_email)
