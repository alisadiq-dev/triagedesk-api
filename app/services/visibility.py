from sqlalchemy import ColumnElement, or_, true

from app.models import Ticket
from app.models.enums import Role
from app.services.permissions import Actor


def ticket_visibility(actor: Actor) -> ColumnElement[bool]:
    """Which tickets the actor may see at all. Others must look nonexistent (404)."""
    if actor.role == Role.CUSTOMER:
        return Ticket.customer_id == actor.id
    if actor.role == Role.AGENT:
        return or_(Ticket.assignee_id.is_(None), Ticket.assignee_id == actor.id)
    return true()
