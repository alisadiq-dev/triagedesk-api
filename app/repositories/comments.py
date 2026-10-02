import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ticket, TicketComment


class CommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, comment: TicketComment) -> None:
        self._session.add(comment)

    async def list_page(
        self, ticket_id: uuid.UUID, include_internal: bool, offset: int, limit: int
    ) -> tuple[list[TicketComment], int]:
        filters = [TicketComment.ticket_id == ticket_id]
        if not include_internal:
            filters.append(TicketComment.is_internal.is_(False))
        total = await self._session.scalar(
            select(func.count()).select_from(TicketComment).where(*filters)
        )
        rows = await self._session.scalars(
            select(TicketComment)
            .where(*filters)
            .order_by(TicketComment.created_at, TicketComment.id)
            .offset(offset)
            .limit(limit)
        )
        return list(rows), total or 0

    async def mark_first_response(self, ticket_id: uuid.UUID, when: datetime) -> None:
        """Set once: a later comment never moves the first-response time."""
        await self._session.execute(
            update(Ticket)
            .where(Ticket.id == ticket_id, Ticket.first_responded_at.is_(None))
            .values(first_responded_at=when)
        )
