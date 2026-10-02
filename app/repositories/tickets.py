import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ticket
from app.models.enums import TicketStatus


class TicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_open_assigned_tickets(self, profile_id: uuid.UUID) -> bool:
        query = select(
            exists().where(Ticket.assignee_id == profile_id, Ticket.status != TicketStatus.CLOSED)
        )
        return bool(await self._session.scalar(query))
