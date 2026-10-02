import uuid

from sqlalchemy import ColumnElement, exists, func, select, update
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

    async def get_plain(self, ticket_id: uuid.UUID) -> Ticket | None:
        """Load a ticket without any visibility rule (for resolving a lost race)."""
        return await self._session.get(Ticket, ticket_id, populate_existing=True)

    async def claim(self, ticket_id: uuid.UUID, assignee_id: uuid.UUID) -> bool:
        """Atomic: only succeeds while the ticket is unassigned and not closed."""
        result = await self._session.execute(
            update(Ticket)
            .where(
                Ticket.id == ticket_id,
                Ticket.assignee_id.is_(None),
                Ticket.status != TicketStatus.CLOSED,
            )
            .values(assignee_id=assignee_id)
            .returning(Ticket.id)
        )
        return result.scalar_one_or_none() is not None

    async def release(self, ticket_id: uuid.UUID, current_assignee_id: uuid.UUID) -> bool:
        """Atomic: only succeeds if the ticket is still held by the expected assignee."""
        result = await self._session.execute(
            update(Ticket)
            .where(
                Ticket.id == ticket_id,
                Ticket.assignee_id == current_assignee_id,
                Ticket.status != TicketStatus.CLOSED,
            )
            .values(assignee_id=None)
            .returning(Ticket.id)
        )
        return result.scalar_one_or_none() is not None

    async def get_locked(self, ticket_id: uuid.UUID) -> Ticket | None:
        """Re-read the ticket and hold a lock (FOR NO KEY UPDATE) until the transaction ends.

        Checks made on this copy cannot be invalidated by a concurrent claim, release, assignment
        or status change before the caller commits.
        """
        query = (
            select(Ticket)
            .where(Ticket.id == ticket_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        return (await self._session.scalars(query)).one_or_none()

    async def reassign(
        self,
        ticket_id: uuid.UUID,
        observed_assignee_id: uuid.UUID | None,
        new_assignee_id: uuid.UUID,
    ) -> bool:
        """Atomic: only succeeds if the assignee is still the one the caller saw and not closed."""
        result = await self._session.execute(
            update(Ticket)
            .where(
                Ticket.id == ticket_id,
                Ticket.assignee_id.is_not_distinct_from(observed_assignee_id),
                Ticket.status != TicketStatus.CLOSED,
            )
            .values(assignee_id=new_assignee_id)
            .returning(Ticket.id)
        )
        return result.scalar_one_or_none() is not None
