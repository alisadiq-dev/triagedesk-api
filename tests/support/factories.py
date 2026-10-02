import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ticket
from app.models.enums import TicketStatus

NOW = datetime.now(UTC)


async def make_ticket(
    session: AsyncSession,
    customer_id: uuid.UUID,
    *,
    status: TicketStatus = TicketStatus.OPEN,
    assignee_id: uuid.UUID | None = None,
    title: str = "Printer is broken",
    description: str = "It prints only blank pages",
    **overrides: object,
) -> Ticket:
    fields: dict[str, object] = {
        "customer_id": customer_id,
        "assignee_id": assignee_id,
        "title": title,
        "description": description,
        "status": status,
        "first_response_due_at": NOW + timedelta(hours=8),
        "resolution_due_at": NOW + timedelta(hours=72),
    }
    if status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
        fields["resolved_at"] = NOW
    fields.update(overrides)
    ticket = Ticket(**fields)
    session.add(ticket)
    await session.commit()
    return ticket
