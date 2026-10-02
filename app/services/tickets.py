import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ticket
from app.models.enums import EventType, Priority, Role, TicketStatus
from app.repositories.config import SlaPolicyRepository
from app.repositories.events import EventRepository
from app.repositories.tickets import TicketRepository, TicketRow
from app.schemas.common import PageParams
from app.schemas.tickets import CUSTOMER_SORTS, TicketCreate, TicketSort
from app.services.errors import InputError, NotFoundError
from app.services.permissions import Actor, require_role
from app.services.sla import compute_deadlines, policy_for
from app.services.visibility import ticket_visibility


class TicketService:
    def __init__(self, session: AsyncSession, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._tickets = TicketRepository(session)
        self._events = EventRepository(session)
        self._policies = SlaPolicyRepository(session)

    async def create(self, data: TicketCreate) -> Ticket:
        require_role(self._actor, Role.CUSTOMER)
        now = datetime.now(UTC)
        policy = await policy_for(self._policies, Priority.MEDIUM)
        first_response_due_at, resolution_due_at = compute_deadlines(policy, now)
        ticket = Ticket(
            id=uuid.uuid4(),
            customer_id=self._actor.id,
            title=data.title,
            description=data.description,
            created_at=now,
            first_response_due_at=first_response_due_at,
            resolution_due_at=resolution_due_at,
        )
        self._tickets.add(ticket)
        self._events.add(
            ticket.id, self._actor.id, EventType.TICKET_CREATED, to_value=TicketStatus.OPEN.value
        )
        await self._session.commit()
        await self._session.refresh(ticket)
        return ticket

    async def get(self, ticket_id: uuid.UUID) -> TicketRow:
        row = await self._tickets.get_with_customer(ticket_id, ticket_visibility(self._actor))
        if row is None:
            raise NotFoundError("Ticket not found")
        return row

    async def list_tickets(
        self, status: TicketStatus | None, sort: TicketSort, page: PageParams
    ) -> tuple[list[TicketRow], int]:
        if self._actor.role == Role.CUSTOMER and sort not in CUSTOMER_SORTS:
            raise InputError("This sort order is not available")
        return await self._tickets.list_page(
            ticket_visibility(self._actor), status, sort, page.offset, page.page_size
        )
