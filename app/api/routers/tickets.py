import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.triage import TriageRunner
from app.api.deps import get_actor, get_session, get_triage_runner
from app.models.enums import Role, TicketStatus
from app.schemas.comments import CommentCreate, CustomerComment, StaffComment
from app.schemas.common import Page, PageParamsDep
from app.schemas.tickets import (
    AssigneeUpdate,
    CustomerTicket,
    SlaStatus,
    StaffTicket,
    StatusChange,
    TicketCreate,
    TicketEventOut,
    TicketOverrides,
    TicketSort,
    render_staff_ticket,
    render_ticket,
)
from app.services.comments import CommentService
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
async def create_ticket(
    body: TicketCreate,
    actor: ActorDep,
    service: Service,
    background_tasks: BackgroundTasks,
    runner: Annotated[TriageRunner, Depends(get_triage_runner)],
) -> CustomerTicket:
    ticket = await service.create(body)
    # Runs after the response is sent, so a slow or failing model never blocks ticket creation.
    background_tasks.add_task(runner.run, ticket.id)
    return render_ticket(actor, ticket, actor.email)


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
    staff_items = [render_staff_ticket(actor, ticket, email) for ticket, email in rows]
    return Page[StaffTicket](
        items=staff_items, page=page.page, page_size=page.page_size, total=total
    )


@router.get("/{ticket_id}", response_model=None)
async def get_ticket(ticket_id: uuid.UUID, actor: ActorDep, service: Service) -> CustomerTicket:
    ticket, email = await service.get(ticket_id)
    return render_ticket(actor, ticket, email)


@router.patch("/{ticket_id}", response_model=None)
async def update_ticket(
    ticket_id: uuid.UUID, body: TicketOverrides, actor: ActorDep, service: Service
) -> CustomerTicket:
    ticket, email = await service.update_overrides(ticket_id, body)
    return render_ticket(actor, ticket, email)


@router.post("/{ticket_id}/claim", response_model=None)
async def claim_ticket(ticket_id: uuid.UUID, actor: ActorDep, service: Service) -> CustomerTicket:
    ticket, email = await service.claim(ticket_id)
    return render_ticket(actor, ticket, email)


@router.post("/{ticket_id}/release", response_model=None)
async def release_ticket(ticket_id: uuid.UUID, actor: ActorDep, service: Service) -> CustomerTicket:
    ticket, email = await service.release(ticket_id)
    return render_ticket(actor, ticket, email)


@router.put("/{ticket_id}/assignee", response_model=None)
async def assign_ticket(
    ticket_id: uuid.UUID, body: AssigneeUpdate, actor: ActorDep, service: Service
) -> CustomerTicket:
    ticket, email = await service.assign(ticket_id, body.assignee_id)
    return render_ticket(actor, ticket, email)


@router.get("/{ticket_id}/comments", response_model=None)
async def list_comments(
    ticket_id: uuid.UUID, actor: ActorDep, session: SessionDep, page: PageParamsDep
) -> Page[CustomerComment] | Page[StaffComment]:
    comments, total = await CommentService(session, actor).list_comments(ticket_id, page)
    if actor.role == Role.CUSTOMER:
        customer_items = [CustomerComment.from_comment(c) for c in comments]
        return Page[CustomerComment](
            items=customer_items, page=page.page, page_size=page.page_size, total=total
        )
    staff_items = [StaffComment.model_validate(c) for c in comments]
    return Page[StaffComment](
        items=staff_items, page=page.page, page_size=page.page_size, total=total
    )


@router.post("/{ticket_id}/comments", status_code=201, response_model=None)
async def add_comment(
    ticket_id: uuid.UUID, body: CommentCreate, actor: ActorDep, session: SessionDep
) -> CustomerComment | StaffComment:
    comment = await CommentService(session, actor).add_comment(ticket_id, body)
    if actor.role == Role.CUSTOMER:
        return CustomerComment.from_comment(comment)
    return StaffComment.model_validate(comment)


@router.post("/{ticket_id}/status", response_model=None)
async def change_status(
    ticket_id: uuid.UUID, body: StatusChange, actor: ActorDep, service: Service
) -> CustomerTicket:
    ticket, email = await service.change_status(ticket_id, body.status)
    return render_ticket(actor, ticket, email)


@router.get("/{ticket_id}/events")
async def list_events(
    ticket_id: uuid.UUID, service: Service, page: PageParamsDep
) -> Page[TicketEventOut]:
    events, total = await service.list_events(ticket_id, page)
    return Page[TicketEventOut](
        items=[TicketEventOut.model_validate(e) for e in events],
        page=page.page,
        page_size=page.page_size,
        total=total,
    )


@router.get("/{ticket_id}/sla")
async def get_sla(ticket_id: uuid.UUID, service: Service) -> SlaStatus:
    ticket = await service.get_for_sla(ticket_id)
    return SlaStatus.from_ticket(ticket, datetime.now(UTC))
