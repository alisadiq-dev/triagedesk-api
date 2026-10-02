import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TicketEvent
from app.models.enums import EventType


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        ticket_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        event_type: EventType,
        from_value: str | None = None,
        to_value: str | None = None,
    ) -> None:
        """Stage an audit row; it is committed with the caller's transaction."""
        self._session.add(
            TicketEvent(
                ticket_id=ticket_id,
                actor_id=actor_id,
                event_type=event_type,
                from_value=from_value,
                to_value=to_value,
            )
        )
