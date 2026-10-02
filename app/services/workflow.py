"""The ticket status workflow as a pure table (no database, no actor)."""

from app.models.enums import TicketStatus
from app.services.errors import InvalidTransitionError

S = TicketStatus

# open -> in_progress -> waiting_on_customer -> in_progress -> resolved -> closed
# Reopen (resolved -> in_progress) is the only way back. closed is final.
_ALLOWED: dict[TicketStatus, set[TicketStatus]] = {
    S.OPEN: {S.IN_PROGRESS},
    S.IN_PROGRESS: {S.WAITING_ON_CUSTOMER, S.RESOLVED},
    S.WAITING_ON_CUSTOMER: {S.IN_PROGRESS},
    S.RESOLVED: {S.CLOSED, S.IN_PROGRESS},
    S.CLOSED: set(),
}


def allowed_targets(current: TicketStatus) -> set[TicketStatus]:
    return set(_ALLOWED[current])


def check_transition(current: TicketStatus, target: TicketStatus) -> None:
    if target in _ALLOWED[current]:
        return
    allowed = ", ".join(sorted(s.value for s in _ALLOWED[current])) or "none (closed is final)"
    raise InvalidTransitionError(
        f"Cannot change status from {current.value} to {target.value}. Allowed: {allowed}"
    )
