import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_actor, get_session
from app.models.enums import Role, TicketStatus
from app.schemas.common import Page, PageParamsDep
from app.schemas.tickets import (
    CustomerTicket,
    StaffTicket,
    TicketCreate,
    TicketSort,
    render_ticket,
)
from app.services.permissions import Actor
from app.services.tickets import TicketService

router = APIRouter(prefix="/tickets", tags=["tickets"])

ActorDep = Annotated[Actor, Depends(get_actor)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_ticket_service(actor: ActorDep, session: SessionDep) -> TicketService:
    return TicketService(session, actor)


Service = Annotated[TicketService, Depends(get_ticket_service)]


# The body depends on the caller's role (customer view or staff view), so the response model is
# not inferred from the annotation; render_ticket picks the right schema.
@router.post("", status_code=201, response_model=None)
async def create_ticket(body: TicketCreate, actor: ActorDep, service: Service) -> CustomerTicket:
    ticket = await service.create(body)
    return render_ticket(actor.role, ticket, actor.email)


@router.get("", response_model=None)
async def list_tickets(
    actor: ActorDep,
    service: Service,
    page: PageParamsDep,
    status: TicketStatus | None = None,
    sort: TicketSort = TicketSort.NEWEST,
) -> Page[CustomerTicket] | Page[StaffTicket]:
    rows, total = await service.list_tickets(status, sort, page)
    if actor.role == Role.CUSTOMER:
        customer_items = [CustomerTicket.model_validate(ticket) for ticket, _ in rows]
        return Page[CustomerTicket](
            items=customer_items, page=page.page, page_size=page.page_size, total=total
        )
    staff_items = [StaffTicket.from_ticket(ticket, email) for ticket, email in rows]
    return Page[StaffTicket](
        items=staff_items, page=page.page, page_size=page.page_size, total=total
    )


@router.get("/{ticket_id}", response_model=None)
async def get_ticket(ticket_id: uuid.UUID, actor: ActorDep, service: Service) -> CustomerTicket:
    ticket, email = await service.get(ticket_id)
    return render_ticket(actor.role, ticket, email)
