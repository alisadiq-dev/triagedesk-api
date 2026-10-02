"""Runs AI triage for one ticket. The model's answer is data: only AI fields are ever written."""

import asyncio
import enum
import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.interface import CategoryOption, TicketText, TriageModel, TriageModelError
from app.ai.keywords import keyword_priority
from app.ai.output import InvalidTriageOutputError, TriageOutput, parse_output
from app.ai.prompts import PROMPT_VERSION
from app.core.db import Database
from app.models import Category, Ticket
from app.models.enums import (
    AiStatus,
    CategorySource,
    EventType,
    Priority,
    PrioritySource,
)
from app.repositories.config import SlaPolicyRepository
from app.repositories.events import EventRepository
from app.repositories.tickets import TicketRepository
from app.services.sla import compute_deadlines, policy_for

logger = logging.getLogger("app.ai")


class Outcome(enum.StrEnum):
    SUCCESS = "success"
    FALLBACK = "fallback"  # the model failed; keyword rules set the priority, ai_status = failed
    SKIPPED = "skipped"  # ticket missing or no longer pending
    FAILED = "failed"  # even writing the result failed; the ticket stays pending


@dataclass(frozen=True)
class _ModelAnswer:
    """What came back from the model call. On failure only the reason and a type and status."""

    output: TriageOutput | None
    reason: str | None = None
    error_type: str | None = None  # exception class name, never its message
    status_code: int | None = None  # HTTP status of a provider error, if there was one


def _describe_error(exc: Exception) -> tuple[str, int | None]:
    """The exception's type name and HTTP status (if it has one). Never its message."""
    if isinstance(exc, TriageModelError):
        return exc.cause_type or type(exc).__name__, exc.status_code
    for attribute in ("status_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return type(exc).__name__, value
    return type(exc).__name__, None


@dataclass(frozen=True)
class _Snapshot:
    ticket: TicketText
    categories: list[CategoryOption]


class TriageRunner:
    def __init__(self, database: Database, model: TriageModel, timeout_seconds: float) -> None:
        self._database = database
        self._model = model
        self._timeout = timeout_seconds

    async def run(self, ticket_id: uuid.UUID) -> Outcome:
        started = time.monotonic()
        outcome = Outcome.FAILED
        reason: str | None = "storage_error"
        error_type: str | None = None
        status_code: int | None = None
        try:
            snapshot = await self._load(ticket_id)
            if snapshot is None:
                outcome, reason = Outcome.SKIPPED, "not_pending"
            else:
                answer = await self._ask_model(snapshot)
                reason, error_type, status_code = (
                    answer.reason,
                    answer.error_type,
                    answer.status_code,
                )
                applied = await self._apply(ticket_id, answer.output, answer.reason)
                if not applied:
                    outcome, reason = Outcome.SKIPPED, "not_pending"
                else:
                    outcome = Outcome.SUCCESS if answer.output is not None else Outcome.FALLBACK
        except Exception as exc:
            error_type, status_code = _describe_error(exc)
            logger.exception("triage_error", extra={"ticket_id": str(ticket_id)})
        logger.info(
            "triage_outcome",
            extra={
                "ticket_id": str(ticket_id),
                "outcome": outcome.value,
                "reason": reason,
                "error_type": error_type,
                "status_code": status_code,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "model": self._model.name,
                "prompt_version": PROMPT_VERSION,
            },
        )
        return outcome

    async def _load(self, ticket_id: uuid.UUID) -> _Snapshot | None:
        """Read what the model needs, then release the connection before the (slow) model call."""
        async with self._database.session() as session:
            ticket = await TicketRepository(session).get_plain(ticket_id)
            if ticket is None or ticket.ai_status != AiStatus.PENDING:
                return None
            rows = await session.scalars(
                select(Category).where(Category.is_active.is_(True)).order_by(Category.id)
            )
            categories = [CategoryOption(c.id, c.name, c.description) for c in rows]
            return _Snapshot(TicketText(ticket.title, ticket.description), categories)

    async def _ask_model(self, snapshot: _Snapshot) -> _ModelAnswer:
        try:
            raw = await asyncio.wait_for(
                self._model.classify(snapshot.ticket, snapshot.categories), self._timeout
            )
        except TimeoutError as exc:
            return _ModelAnswer(None, "timeout", type(exc).__name__)
        except Exception as exc:
            error_type, status_code = _describe_error(exc)
            return _ModelAnswer(None, "model_error", error_type, status_code)
        try:
            result = parse_output(raw)
        except InvalidTriageOutputError:
            return _ModelAnswer(None, "invalid_output")
        allowed = {c.id for c in snapshot.categories}
        if result.category_id is not None and result.category_id not in allowed:
            return _ModelAnswer(None, "invalid_output")
        return _ModelAnswer(result)

    async def _apply(
        self, ticket_id: uuid.UUID, result: TriageOutput | None, reason: str | None
    ) -> bool:
        """Write the outcome. Only AI fields change, and never over a human override."""
        async with self._database.session() as session:
            tickets = TicketRepository(session)
            ticket = await tickets.get_locked(ticket_id)
            if ticket is None or ticket.ai_status != AiStatus.PENDING:
                return False
            events = EventRepository(session)
            if result is not None:
                if (
                    result.category_id is not None
                    and ticket.category_source != CategorySource.HUMAN
                ):
                    ticket.category_id = result.category_id
                    ticket.category_source = CategorySource.AI
                ticket.sentiment = result.sentiment
                ticket.ai_suggested_reply = result.suggested_reply
                ticket.ai_status = AiStatus.COMPLETED
                new_priority: Priority = result.priority
                source = PrioritySource.AI
                summary = (
                    f"priority={result.priority.value} sentiment={result.sentiment.value} "
                    f"category={result.category_id}"
                )
                events.add(ticket.id, None, EventType.TRIAGE_COMPLETED, None, summary)
            else:
                ticket.ai_status = AiStatus.FAILED
                new_priority = keyword_priority(ticket.title, ticket.description)
                source = PrioritySource.KEYWORD
                events.add(ticket.id, None, EventType.TRIAGE_FAILED, None, reason)
            ticket.ai_model = self._model.name
            ticket.ai_prompt_version = PROMPT_VERSION
            if ticket.priority_source != PrioritySource.HUMAN:
                await self._set_priority(session, ticket, new_priority, source)
            await session.commit()
            return True

    @staticmethod
    async def _set_priority(
        session: AsyncSession, ticket: Ticket, priority: Priority, source: PrioritySource
    ) -> None:
        """Set the priority and recalculate both SLA deadlines from created_at."""
        policy = await policy_for(SlaPolicyRepository(session), priority)
        ticket.first_response_due_at, ticket.resolution_due_at = compute_deadlines(
            policy, ticket.created_at
        )
        ticket.priority = priority
        ticket.priority_source = source
