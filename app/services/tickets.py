import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ticket, TicketEvent
from app.models.enums import (
    CategorySource,
    EventType,
    Priority,
    PrioritySource,
    Role,
    TicketStatus,
)
from app.repositories.config import CategoryRepository, SlaPolicyRepository
from app.repositories.events import EventRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.tickets import TicketRepository, TicketRow
from app.schemas.common import PageParams
from app.schemas.tickets import CUSTOMER_SORTS, TicketCreate, TicketOverrides, TicketSort
from app.services.errors import (
    AlreadyAssignedError,
    ConflictError,
    InputError,
    NotFoundError,
    TicketClosedError,
)
from app.services.permissions import Actor, ForbiddenError, require_role
from app.services.sla import compute_deadlines, policy_for
from app.services.visibility import ticket_visibility
from app.services.workflow import check_transition


class TicketService:
    def __init__(self, session: AsyncSession, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._tickets = TicketRepository(session)
        self._events = EventRepository(session)
        self._policies = SlaPolicyRepository(session)
        self._categories = CategoryRepository(session)
        self._profiles = ProfileRepository(session)

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

    # --- changes by staff ---------------------------------------------------------------------

    async def update_overrides(self, ticket_id: uuid.UUID, data: TicketOverrides) -> TicketRow:
        ticket = await self._ticket_for_staff_change(ticket_id)
        if data.category_id is not None and data.category_id != ticket.category_id:
            category = await self._categories.get(data.category_id)
            if category is None or not category.is_active:
                raise InputError("category_id is not an active category")
            self._events.add(
                ticket.id, self._actor.id, EventType.CATEGORY_CHANGED,
                _text(ticket.category_id), str(category.id),
            )  # fmt: skip
            ticket.category_id = category.id
            ticket.category_source = CategorySource.HUMAN
        if data.priority is not None and data.priority != ticket.priority:
            policy = await policy_for(self._policies, data.priority)
            ticket.first_response_due_at, ticket.resolution_due_at = compute_deadlines(
                policy, ticket.created_at
            )
            self._events.add(
                ticket.id, self._actor.id, EventType.PRIORITY_CHANGED,
                ticket.priority.value, data.priority.value,
            )  # fmt: skip
            ticket.priority = data.priority
            ticket.priority_source = PrioritySource.HUMAN
        await self._session.commit()
        return await self.get(ticket_id)

    async def change_status(self, ticket_id: uuid.UUID, target: TicketStatus) -> TicketRow:
        ticket = await self._ticket_for_staff_change(ticket_id)  # 404, 403, 409 closed
        check_transition(ticket.status, target)  # 409 invalid_transition
        previous = ticket.status
        if previous == TicketStatus.RESOLVED and target == TicketStatus.IN_PROGRESS:
            # Reopen: clear resolved_at, but keep the old value in the audit log.
            self._events.add(
                ticket.id, self._actor.id, EventType.RESOLVED_AT_CLEARED,
                _text(ticket.resolved_at.isoformat() if ticket.resolved_at else None), None,
            )  # fmt: skip
            ticket.resolved_at = None
        if target == TicketStatus.RESOLVED:
            ticket.resolved_at = datetime.now(UTC)
        ticket.status = target
        self._events.add(
            ticket.id, self._actor.id, EventType.STATUS_CHANGED, previous.value, target.value
        )
        await self._session.commit()
        return await self.get(ticket_id)

    async def list_events(
        self, ticket_id: uuid.UUID, page: PageParams
    ) -> tuple[list[TicketEvent], int]:
        await self.get(ticket_id)  # 404 when the ticket is not visible
        require_role(self._actor, Role.AGENT, Role.ADMIN)
        return await self._events.list_page(ticket_id, page.offset, page.page_size)

    async def claim(self, ticket_id: uuid.UUID) -> TicketRow:
        ticket, _ = await self.get(ticket_id)
        if self._actor.role == Role.CUSTOMER or ticket.customer_id == self._actor.id:
            raise ForbiddenError("You do not have permission to do this")
        # Re-read our own role under a share lock: a concurrent role change must wait for us.
        me = await self._profiles.get_locked(self._actor.id, exclusive=False)
        if me is None or me.role not in (Role.AGENT, Role.ADMIN):
            raise ForbiddenError("You do not have permission to do this")
        if ticket.status == TicketStatus.CLOSED:
            raise TicketClosedError
        if not await self._tickets.claim(ticket_id, self._actor.id):
            current = await self._tickets.get_plain(ticket_id)
            if current is not None and current.status == TicketStatus.CLOSED:
                raise TicketClosedError
            raise AlreadyAssignedError
        self._events.add(ticket_id, self._actor.id, EventType.ASSIGNED, None, str(self._actor.id))
        await self._session.commit()
        return await self.get(ticket_id)

    async def release(self, ticket_id: uuid.UUID) -> TicketRow:
        ticket = await self._ticket_for_staff_change(ticket_id)
        previous_assignee = ticket.assignee_id  # the UPDATE below also clears the loaded object
        if previous_assignee is None:
            return await self.get(ticket_id)  # nothing to release
        if not await self._tickets.release(ticket_id, previous_assignee):
            raise ConflictError("The ticket assignment changed; try again")
        self._events.add(
            ticket_id, self._actor.id, EventType.RELEASED, str(previous_assignee), None
        )
        await self._session.commit()
        return await self.get(ticket_id)

    async def assign(self, ticket_id: uuid.UUID, assignee_id: uuid.UUID) -> TicketRow:
        ticket, _ = await self.get(ticket_id)
        require_role(self._actor, Role.ADMIN)
        if ticket.status == TicketStatus.CLOSED:
            raise TicketClosedError
        # Share lock: the assignee cannot be demoted between this check and our commit.
        assignee = await self._profiles.get_locked(assignee_id, exclusive=False)
        if assignee is None or assignee.role not in (Role.AGENT, Role.ADMIN):
            raise InputError("assignee_id must be an agent or an admin")
        if assignee.id == ticket.customer_id:
            raise InputError("A ticket cannot be assigned to its own customer")
        observed = ticket.assignee_id
        if observed != assignee.id:
            if not await self._tickets.reassign(ticket_id, observed, assignee.id):
                current = await self._tickets.get_plain(ticket_id)
                if current is not None and current.status == TicketStatus.CLOSED:
                    raise TicketClosedError
                raise ConflictError("The ticket assignment changed; try again")
            self._events.add(
                ticket_id, self._actor.id, EventType.ASSIGNED, _text(observed), str(assignee.id)
            )
            await self._session.commit()
        return await self.get(ticket_id)

    async def _ticket_for_staff_change(self, ticket_id: uuid.UUID) -> Ticket:
        """404 if invisible, 403 if not allowed to change it, 409 if closed (in that order).

        The checks run on a locked, freshly read copy, so a concurrent reassignment or status
        change cannot invalidate them before this transaction commits.
        """
        await self.get(ticket_id)
        ticket = await self._tickets.get_locked(ticket_id)
        if ticket is None:
            raise NotFoundError("Ticket not found")
        if self._actor.role == Role.CUSTOMER:
            raise ForbiddenError("You do not have permission to do this")
        if self._actor.role == Role.AGENT and ticket.assignee_id != self._actor.id:
            raise ForbiddenError("Claim the ticket first; only the assignee can change it")
        if ticket.status == TicketStatus.CLOSED:
            raise TicketClosedError
        return ticket


def _text(value: object) -> str | None:
    return None if value is None else str(value)
