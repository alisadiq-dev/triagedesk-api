import pytest

from app.models.enums import TicketStatus
from app.services.errors import InvalidTransitionError
from app.services.workflow import allowed_targets, check_transition

S = TicketStatus

VALID = {
    (S.OPEN, S.IN_PROGRESS),
    (S.IN_PROGRESS, S.WAITING_ON_CUSTOMER),
    (S.IN_PROGRESS, S.RESOLVED),
    (S.WAITING_ON_CUSTOMER, S.IN_PROGRESS),
    (S.RESOLVED, S.CLOSED),
    (S.RESOLVED, S.IN_PROGRESS),  # reopen
}
ALL_PAIRS = [(a, b) for a in TicketStatus for b in TicketStatus]


@pytest.mark.parametrize(("current", "target"), sorted(VALID))
def test_valid_transitions_are_allowed(current: TicketStatus, target: TicketStatus) -> None:
    check_transition(current, target)


@pytest.mark.parametrize(("current", "target"), [pair for pair in ALL_PAIRS if pair not in VALID])
def test_every_other_pair_is_rejected_with_a_clear_message(
    current: TicketStatus, target: TicketStatus
) -> None:
    with pytest.raises(InvalidTransitionError) as caught:
        check_transition(current, target)

    assert caught.value.status_code == 409
    assert caught.value.code == "invalid_transition"
    assert current.value in caught.value.message
    assert target.value in caught.value.message


def test_the_table_covers_all_25_pairs_exactly_once() -> None:
    assert len(ALL_PAIRS) == 25
    assert len(VALID) == 6


def test_closed_is_final() -> None:
    assert allowed_targets(TicketStatus.CLOSED) == set()


def test_the_message_lists_what_is_allowed_instead() -> None:
    with pytest.raises(InvalidTransitionError) as caught:
        check_transition(TicketStatus.OPEN, TicketStatus.RESOLVED)

    assert "in_progress" in caught.value.message
