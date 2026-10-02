import uuid

from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Profile, Ticket
from app.models.enums import TicketStatus
from app.schemas.tickets import TicketSort

_SORT_COLUMNS = {
    TicketSort.NEWEST: Ticket.created_at.desc(),
    TicketSort.OLDEST: Ticket.created_at.asc(),
    TicketSort.FIRST_RESPONSE_DUE: Ticket.first_response_due_at.asc(),
    TicketSort.RESOLUTION_DUE: Ticket.resolution_due_at.asc(),
}

TicketRow = tuple[Ticket, str | None]


class TicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, ticket: Ticket) -> None:
        self._session.add(ticket)

    async def has_open_assigned_tickets(self, profile_id: uuid.UUID) -> bool:
        query = select(
            exists().where(Ticket.assignee_id == profile_id, Ticket.status != TicketStatus.CLOSED)
        )
        return bool(await self._session.scalar(query))

    async def get_with_customer(
        self, ticket_id: uuid.UUID, visible: ColumnElement[bool]
    ) -> TicketRow | None:
        """The ticket and its customer's email in one query, only if it is visible to the caller."""
        query = (
            select(Ticket, Profile.email)
            .join(Profile, Profile.id == Ticket.customer_id)
            .where(Ticket.id == ticket_id, visible)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(query)).first()
        return None if row is None else (row[0], row[1])

    async def list_page(
        self,
        visible: ColumnElement[bool],
        status: TicketStatus | None,
        sort: TicketSort,
        offset: int,
        limit: int,
    ) -> tuple[list[TicketRow], int]:
        filters = [visible]
        if status is not None:
            filters.append(Ticket.status == status)
        total = await self._session.scalar(select(func.count()).select_from(Ticket).where(*filters))
        query = (
            select(Ticket, Profile.email)
            .join(Profile, Profile.id == Ticket.customer_id)
            .where(*filters)
            .order_by(_SORT_COLUMNS[sort], Ticket.id)
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(query)).all()
        return [(row[0], row[1]) for row in rows], total or 0
