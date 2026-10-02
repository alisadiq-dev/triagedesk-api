import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import DateTime, cast, func, literal, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Profile, SlaPolicy, Ticket, TicketComment, TicketEvent
from app.models.enums import (
    AiStatus,
    EventType,
    Priority,
    PrioritySource,
    Role,
    TicketStatus,
)

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


def make_profile(role: Role = Role.CUSTOMER) -> Profile:
    return Profile(id=uuid.uuid4(), role=role)


def make_ticket(customer: Profile, **overrides: object) -> Ticket:
    fields: dict[str, object] = {
        "customer_id": customer.id,
        "title": "Printer is broken",
        "description": "It prints only blank pages since Monday",
        "first_response_due_at": NOW + timedelta(hours=8),
        "resolution_due_at": NOW + timedelta(hours=72),
    }
    fields.update(overrides)
    return Ticket(**fields)


async def expect_rejected(session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_new_profile_defaults_to_the_customer_role(session: AsyncSession) -> None:
    profile = Profile(id=uuid.uuid4())
    session.add(profile)
    await session.commit()
    await session.refresh(profile)

    assert profile.role == Role.CUSTOMER


async def test_unknown_role_is_rejected(session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await session.execute(
            text("INSERT INTO profiles (id, role) VALUES (:id, 'superuser')"), {"id": uuid.uuid4()}
        )
    await session.rollback()


async def test_category_names_are_unique_ignoring_case(session: AsyncSession) -> None:
    session.add(Category(name="Billing"))
    await session.commit()

    session.add(Category(name="billing"))

    await expect_rejected(session)


@pytest.mark.parametrize(("response", "resolution"), [(0, 4), (-1, 4), (8, 4)])
async def test_sla_policy_rejects_non_positive_or_inverted_hours(
    session: AsyncSession, response: int, resolution: int
) -> None:
    session.add(
        SlaPolicy(priority=Priority.HIGH, response_hours=response, resolution_hours=resolution)
    )

    await expect_rejected(session)


async def test_new_ticket_gets_the_documented_defaults(session: AsyncSession) -> None:
    customer = make_profile()
    session.add(customer)
    ticket = make_ticket(customer)
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)

    assert ticket.status == TicketStatus.OPEN
    assert ticket.priority == Priority.MEDIUM
    assert ticket.priority_source == PrioritySource.DEFAULT
    assert ticket.ai_status == AiStatus.PENDING
    assert ticket.category_id is None
    assert ticket.assignee_id is None


@pytest.mark.parametrize(
    ("status", "resolved_at", "allowed"),
    [
        (TicketStatus.OPEN, None, True),
        (TicketStatus.IN_PROGRESS, None, True),
        (TicketStatus.WAITING_ON_CUSTOMER, None, True),
        (TicketStatus.RESOLVED, NOW, True),
        (TicketStatus.CLOSED, NOW, True),
        (TicketStatus.RESOLVED, None, False),
        (TicketStatus.CLOSED, None, False),
        (TicketStatus.OPEN, NOW, False),
        (TicketStatus.IN_PROGRESS, NOW, False),
    ],
)
async def test_resolved_at_is_set_exactly_when_status_is_resolved_or_closed(
    session: AsyncSession, status: TicketStatus, resolved_at: datetime | None, allowed: bool
) -> None:
    customer = make_profile()
    session.add_all([customer, make_ticket(customer, status=status, resolved_at=resolved_at)])

    if allowed:
        await session.commit()
    else:
        await expect_rejected(session)


async def test_empty_description_is_rejected(session: AsyncSession) -> None:
    customer = make_profile()
    session.add_all([customer, make_ticket(customer, description="")])

    await expect_rejected(session)


async def test_a_profile_with_tickets_cannot_be_deleted(session: AsyncSession) -> None:
    customer = make_profile()
    session.add_all([customer, make_ticket(customer)])
    await session.commit()

    with pytest.raises(IntegrityError):
        await session.execute(text("DELETE FROM profiles WHERE id = :id"), {"id": customer.id})
    await session.rollback()


@pytest.mark.parametrize(("word", "expected"), [("blank", 1), ("printer", 1), ("scanner", 0)])
async def test_full_text_search_matches_title_and_description_words(
    session: AsyncSession, word: str, expected: int
) -> None:
    customer = make_profile()
    session.add_all([customer, make_ticket(customer)])
    await session.commit()

    query = select(func.count()).where(
        Ticket.search_vector.op("@@")(func.plainto_tsquery("english", word))
    )

    assert await session.scalar(query) == expected


BREACH_CASES = [
    # (first_responded_offset_h, resolved_offset_h, status, check_time_offset_h, expected)
    pytest.param(None, None, TicketStatus.OPEN, 1, False, id="nothing-due-yet"),
    pytest.param(None, None, TicketStatus.OPEN, 9, True, id="no-first-response-after-deadline"),
    pytest.param(
        2, None, TicketStatus.IN_PROGRESS, 9, False, id="responded-in-time-not-yet-resolution-due"
    ),
    pytest.param(
        2, None, TicketStatus.IN_PROGRESS, 73, True, id="unresolved-after-resolution-deadline"
    ),
    pytest.param(2, 10, TicketStatus.RESOLVED, 100, False, id="resolved-in-time"),
    pytest.param(None, 10, TicketStatus.RESOLVED, 9, True, id="resolved-but-never-answered"),
    pytest.param(
        2, None, TicketStatus.IN_PROGRESS, 72, False, id="exactly-at-deadline-is-not-breached"
    ),
]


@pytest.mark.parametrize(
    ("responded_h", "resolved_h", "status", "check_h", "expected"), BREACH_CASES
)
async def test_sql_breach_clause_agrees_with_the_python_rule(
    session: AsyncSession,
    responded_h: int | None,
    resolved_h: int | None,
    status: TicketStatus,
    check_h: int,
    expected: bool,
) -> None:
    customer = make_profile()
    ticket = make_ticket(
        customer,
        status=status,
        first_responded_at=None if responded_h is None else NOW + timedelta(hours=responded_h),
        resolved_at=None if resolved_h is None else NOW + timedelta(hours=resolved_h),
    )
    session.add_all([customer, ticket])
    await session.commit()
    check_time = NOW + timedelta(hours=check_h)

    in_sql = await session.scalar(
        select(Ticket.breached_clause(cast(literal(check_time), DateTime(timezone=True))))
    )

    assert ticket.is_breached_at(check_time) is expected
    assert in_sql is expected


async def test_breached_tickets_can_be_filtered_in_the_database(session: AsyncSession) -> None:
    customer = make_profile()
    overdue = make_ticket(
        customer,
        first_response_due_at=datetime.now(UTC) - timedelta(hours=1),
        resolution_due_at=datetime.now(UTC) + timedelta(hours=10),
    )
    on_track = make_ticket(
        customer,
        first_response_due_at=datetime.now(UTC) + timedelta(hours=1),
        resolution_due_at=datetime.now(UTC) + timedelta(hours=10),
    )
    session.add_all([customer, overdue, on_track])
    await session.commit()

    ids = (await session.scalars(select(Ticket.id).where(Ticket.breached_clause()))).all()

    assert ids == [overdue.id]
    assert overdue.is_breached is True
    assert on_track.is_breached is False


async def test_customers_cannot_write_internal_comments(session: AsyncSession) -> None:
    customer = make_profile()
    ticket = make_ticket(customer)
    session.add_all([customer, ticket])
    await session.commit()

    session.add(
        TicketComment(
            ticket_id=ticket.id,
            author_id=customer.id,
            author_role=Role.CUSTOMER,
            body="note",
            is_internal=True,
        )
    )

    await expect_rejected(session)


async def test_agents_can_write_internal_comments(session: AsyncSession) -> None:
    customer, agent = make_profile(), make_profile(Role.AGENT)
    ticket = make_ticket(customer)
    session.add_all([customer, agent, ticket])
    await session.commit()

    session.add(
        TicketComment(
            ticket_id=ticket.id,
            author_id=agent.id,
            author_role=Role.AGENT,
            body="Looks like a driver issue",
            is_internal=True,
        )
    )

    await session.commit()


async def test_events_record_who_what_from_to_and_when(session: AsyncSession) -> None:
    customer, agent = make_profile(), make_profile(Role.AGENT)
    ticket = make_ticket(customer)
    session.add_all([customer, agent, ticket])
    await session.commit()
    event = TicketEvent(
        ticket_id=ticket.id,
        actor_id=agent.id,
        event_type=EventType.STATUS_CHANGED,
        from_value="open",
        to_value="in_progress",
    )
    system_event = TicketEvent(ticket_id=ticket.id, event_type=EventType.TRIAGE_COMPLETED)
    session.add_all([event, system_event])
    await session.commit()
    await session.refresh(event)

    assert event.id is not None
    assert event.created_at is not None
    assert system_event.actor_id is None


async def test_expected_indexes_exist(session: AsyncSession) -> None:
    rows = await session.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
    )
    definitions = {name: definition for name, definition in rows.all()}

    expected = {
        "ix_tickets_status",
        "ix_tickets_assignee_id",
        "ix_tickets_created_at",
        "ix_tickets_customer_id_created_at",
        "ix_tickets_first_response_due_at_unanswered",
        "ix_tickets_resolution_due_at_unresolved",
        "ix_tickets_search_vector",
        "ix_ticket_comments_ticket_id_created_at",
        "ix_ticket_events_ticket_id_created_at",
    }
    assert expected <= set(definitions)
    assert (
        "first_responded_at IS NULL" in definitions["ix_tickets_first_response_due_at_unanswered"]
    )
    assert "resolved_at IS NULL" in definitions["ix_tickets_resolution_due_at_unresolved"]
    assert "USING gin" in definitions["ix_tickets_search_vector"]
