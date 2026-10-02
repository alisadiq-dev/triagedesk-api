import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TicketComment
from app.models.enums import Role, TicketStatus
from app.repositories.comments import CommentRepository
from app.repositories.tickets import TicketRepository
from app.schemas.comments import CommentCreate
from app.schemas.common import PageParams
from app.services.errors import NotFoundError, TicketClosedError
from app.services.permissions import Actor, ForbiddenError
from app.services.tickets import TicketService


class CommentService:
    def __init__(self, session: AsyncSession, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._tickets = TicketService(session, actor)
        self._comments = CommentRepository(session)
        self._ticket_repo = TicketRepository(session)

    async def list_comments(
        self, ticket_id: uuid.UUID, page: PageParams
    ) -> tuple[list[TicketComment], int]:
        await self._tickets.get(ticket_id)  # 404 when the ticket is not visible
        return await self._comments.list_page(
            ticket_id,
            include_internal=self._actor.role != Role.CUSTOMER,
            offset=page.offset,
            limit=page.page_size,
        )

    async def add_comment(self, ticket_id: uuid.UUID, data: CommentCreate) -> TicketComment:
        await self._tickets.get(ticket_id)  # 404 when the ticket is not visible
        # Check against a locked, fresh copy so a concurrent reassignment or close cannot slip by.
        ticket = await self._ticket_repo.get_locked(ticket_id)
        if ticket is None:
            raise NotFoundError("Ticket not found")
        self._check_may_comment(assignee_id=ticket.assignee_id, is_internal=data.is_internal)
        if ticket.status == TicketStatus.CLOSED:
            raise TicketClosedError
        comment = TicketComment(
            id=uuid.uuid4(),
            ticket_id=ticket_id,
            author_id=self._actor.id,
            author_role=self._actor.role,
            body=data.body,
            is_internal=data.is_internal,
        )
        self._comments.add(comment)
        counts_as_first_response = (
            self._actor.role != Role.CUSTOMER
            and not data.is_internal
            and ticket.customer_id
            != self._actor.id  # staff answering their own ticket does not count
        )
        if counts_as_first_response:
            await self._comments.mark_first_response(ticket_id, datetime.now(UTC))
        await self._session.commit()
        await self._session.refresh(comment)
        return comment

    def _check_may_comment(self, assignee_id: uuid.UUID | None, is_internal: bool) -> None:
        if self._actor.role == Role.CUSTOMER and is_internal:
            raise ForbiddenError("Customers cannot write internal notes")
        if self._actor.role == Role.AGENT and assignee_id != self._actor.id:
            raise ForbiddenError("Claim the ticket first; only the assignee can comment")
