import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, exists, func, not_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Profile, Ticket
from app.models.enums import AiStatus, TicketStatus
from app.schemas.tickets import TicketFilters, TicketSort

_SORT_COLUMNS = {
    TicketSort.NEWEST: Ticket.created_at.desc(),
    TicketSort.OLDEST: Ticket.created_at.asc(),
    TicketSort.FIRST_RESPONSE_DUE: Ticket.first_response_due_at.asc(),
    TicketSort.RESOLUTION_DUE: Ticket.resolution_due_at.asc(),
}

TicketRow = tuple[Ticket, str | None]


def _filter_conditions(filters: TicketFilters) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if filters.status is not None:
        conditions.append(Ticket.status == filters.status)
    if filters.priority is not None:
        conditions.append(Ticket.priority == filters.priority)
    if filters.category_id is not None:
        conditions.append(Ticket.category_id == filters.category_id)
    if filters.assignee_id is not None:
        conditions.append(Ticket.assignee_id == filters.assignee_id)
    if filters.unassigned is not None:
        conditions.append(
            Ticket.assignee_id.is_(None) if filters.unassigned else Ticket.assignee_id.is_not(None)
        )
    if filters.sla_breached is not None:
        breached = Ticket.breached_clause()
        conditions.append(breached if filters.sla_breached else not_(breached))
    if filters.q is not None:
        # plainto_tsquery treats the text as plain words, never as query syntax.
        conditions.append(Ticket.search_vector.op("@@")(func.plainto_tsquery("english", filters.q)))
    if filters.created_after is not None:
        conditions.append(Ticket.created_at >= filters.created_after)
    if filters.created_before is not None:
        conditions.append(Ticket.created_at < filters.created_before)
    return conditions


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
        filters: TicketFilters,
        sort: TicketSort,
        offset: int,
        limit: int,
    ) -> tuple[list[TicketRow], int]:
        conditions = [visible, *_filter_conditions(filters)]
        total = await self._session.scalar(
            select(func.count()).select_from(Ticket).where(*conditions)
        )
        query = (
            select(Ticket, Profile.email)
            .join(Profile, Profile.id == Ticket.customer_id)
            .where(*conditions)
            .order_by(_SORT_COLUMNS[sort], Ticket.id)
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(query)).all()
        return [(row[0], row[1]) for row in rows], total or 0

    async def get_plain(self, ticket_id: uuid.UUID) -> Ticket | None:
        """Load a ticket without any visibility rule (for resolving a lost race)."""
        return await self._session.get(Ticket, ticket_id, populate_existing=True)

    async def stale_pending_ids(self, created_before: datetime, limit: int) -> list[uuid.UUID]:
        """Tickets whose triage never finished, oldest first (for the recovery sweeper)."""
        query = (
            select(Ticket.id)
            .where(Ticket.ai_status == AiStatus.PENDING, Ticket.created_at < created_before)
            .order_by(Ticket.created_at, Ticket.id)
            .limit(limit)
        )
        return list(await self._session.scalars(query))

    async def try_triage_lock(self, ticket_id: uuid.UUID) -> bool:
        """Session-level advisory lock; must be released on the same connection."""
        query = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
        return bool(await self._session.scalar(query, {"key": f"triage:{ticket_id}"}))

    async def release_triage_lock(self, ticket_id: uuid.UUID) -> None:
        query = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")
        await self._session.execute(query, {"key": f"triage:{ticket_id}"})

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
