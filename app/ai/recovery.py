"""Recovers tickets stuck in ai_status = pending after a lost background task (ADR 0006)."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.ai.triage import Outcome
from app.core.db import Database
from app.repositories.tickets import TicketRepository

logger = logging.getLogger("app.ai")


@dataclass(frozen=True)
class RecoveryResult:
    found: int
    recovered: int


class TicketTriager(Protocol):
    async def run(self, ticket_id: uuid.UUID) -> Outcome: ...


class TriageRecovery:
    def __init__(
        self,
        database: Database,
        runner: TicketTriager,
        age_seconds: int,
        batch_size: int,
        max_attempts: int,
    ) -> None:
        self._database = database
        self._runner = runner
        self._age = timedelta(seconds=age_seconds)
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        # Tickets whose result could not be stored, so they would be paid for again every sweep.
        # In memory: a restart gives them a fresh start.
        self._failed_attempts: dict[uuid.UUID, int] = {}

    async def sweep(self) -> RecoveryResult:
        """One pass: triage up to a batch of old pending tickets, then log one line."""
        cutoff = datetime.now(UTC) - self._age
        given_up = {t for t, n in self._failed_attempts.items() if n >= self._max_attempts}
        async with self._database.session() as session:
            ticket_ids = await TicketRepository(session).stale_pending_ids(
                cutoff, self._batch_size, exclude=given_up
            )
        recovered = 0
        for ticket_id in ticket_ids:
            outcome = await self._recover(ticket_id)
            if outcome in (Outcome.SUCCESS, Outcome.FALLBACK):
                recovered += 1
                self._failed_attempts.pop(ticket_id, None)
            elif outcome == Outcome.FAILED:
                self._failed_attempts[ticket_id] = self._failed_attempts.get(ticket_id, 0) + 1
        result = RecoveryResult(found=len(ticket_ids), recovered=recovered)
        logger.info("triage_recovery", extra={"found": result.found, "recovered": result.recovered})
        return result

    async def _recover(self, ticket_id: uuid.UUID) -> Outcome:
        """Run triage while holding a per-ticket advisory lock, so two sweepers never both pay."""
        async with self._database.session() as session:
            tickets = TicketRepository(session)
            if not await tickets.try_triage_lock(ticket_id):
                return Outcome.SKIPPED
            try:
                return await self._runner.run(ticket_id)
            finally:
                try:
                    await tickets.release_triage_lock(ticket_id)
                except Exception:
                    # Never hand a connection that may still hold the lock back to the pool.
                    await session.invalidate()
                    logger.exception("triage_lock_release_failed")


async def run_periodically(
    sweep: Callable[[], Awaitable[RecoveryResult]], interval_seconds: float
) -> None:
    """Sweep at startup, then every interval. One failed sweep never stops the loop."""
    while True:
        try:
            await sweep()
        except Exception:
            logger.exception("triage_recovery_error")
        await asyncio.sleep(interval_seconds)
