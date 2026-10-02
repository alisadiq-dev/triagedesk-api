import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import update

from app.ai.interface import TriageModel
from app.ai.recovery import RecoveryResult, TriageRecovery, run_periodically
from app.ai.triage import TriageRunner
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models import Ticket
from app.models.enums import AiStatus
from tests.support.ai import ScriptedModel, good_answer, model_unavailable
from tests.support.factories import make_ticket
from tests.support.world import World

RecoveryFactory = Callable[..., TriageRecovery]


def minutes_ago(minutes: float) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


@pytest.fixture
async def make_recovery(fresh_database_url: str) -> AsyncIterator[RecoveryFactory]:
    databases: list[Database] = []

    def build(model: TriageModel, age_seconds: int = 120, batch_size: int = 10) -> TriageRecovery:
        settings = Settings(_env_file=None, database_url=SecretStr(fresh_database_url))
        database = Database(settings)
        databases.append(database)
        runner = TriageRunner(database, model, timeout_seconds=2.0)
        return TriageRecovery(database, runner, age_seconds, batch_size)

    yield build
    for database in databases:
        await database.dispose()


async def status_of(world: World, ticket_id: object) -> AiStatus:
    world.session.expire_all()
    row = await world.session.get(Ticket, ticket_id)
    assert row is not None
    return row.ai_status


async def test_an_old_pending_ticket_is_triaged_as_after_a_lost_background_task(
    world: World, make_recovery: RecoveryFactory
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], created_at=minutes_ago(10))
    ticket_id = ticket.id
    recovery = make_recovery(ScriptedModel(good_answer(None)))

    result = await recovery.sweep()

    assert result == RecoveryResult(found=1, recovered=1)
    assert await status_of(world, ticket_id) == AiStatus.COMPLETED


async def test_a_recent_pending_ticket_is_left_alone(
    world: World, make_recovery: RecoveryFactory
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], created_at=minutes_ago(0.5))
    ticket_id = ticket.id
    model = ScriptedModel(good_answer(None))

    result = await make_recovery(model).sweep()

    assert result == RecoveryResult(found=0, recovered=0)
    assert model.calls == []
    assert await status_of(world, ticket_id) == AiStatus.PENDING


@pytest.mark.parametrize("done", [AiStatus.COMPLETED, AiStatus.FAILED])
async def test_finished_tickets_are_left_alone(
    world: World, make_recovery: RecoveryFactory, done: AiStatus
) -> None:
    await make_ticket(
        world.session, world.ids["customer"], created_at=minutes_ago(10), ai_status=done
    )
    model = ScriptedModel(good_answer(None))

    result = await make_recovery(model).sweep()

    assert result == RecoveryResult(found=0, recovered=0)
    assert model.calls == []


async def test_the_batch_limit_is_respected_and_the_oldest_tickets_go_first(
    world: World, make_recovery: RecoveryFactory
) -> None:
    newest = await make_ticket(world.session, world.ids["customer"], created_at=minutes_ago(10))
    oldest = await make_ticket(world.session, world.ids["customer"], created_at=minutes_ago(30))
    middle = await make_ticket(world.session, world.ids["customer"], created_at=minutes_ago(20))
    ids = (newest.id, oldest.id, middle.id)

    result = await make_recovery(ScriptedModel(good_answer(None)), batch_size=2).sweep()

    assert result == RecoveryResult(found=2, recovered=2)
    assert await status_of(world, ids[1]) == AiStatus.COMPLETED
    assert await status_of(world, ids[2]) == AiStatus.COMPLETED
    assert await status_of(world, ids[0]) == AiStatus.PENDING


async def test_two_concurrent_sweepers_call_the_model_once(
    world: World, make_recovery: RecoveryFactory
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], created_at=minutes_ago(10))
    ticket_id = ticket.id
    model = ScriptedModel(good_answer(None), delay=0.4)
    first, second = make_recovery(model), make_recovery(model)

    await asyncio.gather(first.sweep(), second.sweep())

    assert len(model.calls) == 1
    assert await status_of(world, ticket_id) == AiStatus.COMPLETED


async def test_a_failing_model_is_recovered_through_the_keyword_fallback(
    world: World, make_recovery: RecoveryFactory
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], created_at=minutes_ago(10))
    ticket_id = ticket.id

    result = await make_recovery(model_unavailable()).sweep()

    assert result == RecoveryResult(found=1, recovered=1)
    assert await status_of(world, ticket_id) == AiStatus.FAILED


async def test_the_lock_is_released_so_a_later_sweep_can_take_a_ticket_again(
    world: World, make_recovery: RecoveryFactory
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], created_at=minutes_ago(10))
    recovery = make_recovery(ScriptedModel(good_answer(None)))
    await recovery.sweep()
    await world.session.execute(  # put it back to pending, as a restart-and-lose scenario
        update(Ticket).where(Ticket.id == ticket.id).values(ai_status=AiStatus.PENDING)
    )
    await world.session.commit()

    result = await recovery.sweep()

    assert result == RecoveryResult(found=1, recovered=1)


async def test_each_sweep_logs_one_line_with_counts_and_no_ticket_text(
    world: World, make_recovery: RecoveryFactory, caplog: pytest.LogCaptureFixture
) -> None:
    await make_ticket(
        world.session,
        world.ids["customer"],
        created_at=minutes_ago(10),
        title="SECRET-TITLE",
        description="SECRET-BODY",
    )
    recovery = make_recovery(ScriptedModel(good_answer(None)))

    with caplog.at_level(logging.INFO, logger="app.ai"):
        await recovery.sweep()

    records = [r for r in caplog.records if r.getMessage() == "triage_recovery"]
    assert len(records) == 1
    assert (records[0].__dict__["found"], records[0].__dict__["recovered"]) == (1, 1)
    assert "SECRET-TITLE" not in caplog.text
    assert "SECRET-BODY" not in caplog.text


async def test_a_sweep_that_finds_nothing_still_logs_its_line(
    world: World, make_recovery: RecoveryFactory, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.ai"):
        await make_recovery(ScriptedModel(good_answer(None))).sweep()

    records = [r for r in caplog.records if r.getMessage() == "triage_recovery"]
    assert [(r.__dict__["found"], r.__dict__["recovered"]) for r in records] == [(0, 0)]


# --- the periodic loop ---------------------------------------------------------------------


async def test_the_loop_sweeps_at_startup_and_then_on_every_interval() -> None:
    sweeps: list[int] = []

    async def sweep() -> RecoveryResult:
        sweeps.append(1)
        return RecoveryResult(0, 0)

    task = asyncio.create_task(run_periodically(sweep, interval_seconds=0.05))
    await asyncio.sleep(0.3)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert len(sweeps) >= 3


async def test_a_failing_sweep_does_not_stop_the_loop(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[int] = []

    async def sweep() -> RecoveryResult:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database restarting")
        return RecoveryResult(0, 0)

    with caplog.at_level(logging.INFO, logger="app.ai"):
        task = asyncio.create_task(run_periodically(sweep, interval_seconds=0.05))
        await asyncio.sleep(0.3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert len(calls) >= 2
    assert any(r.getMessage() == "triage_recovery_error" for r in caplog.records)


# --- app lifespan --------------------------------------------------------------------------


def lifespan_settings(url: str, enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        database_url=SecretStr(url),
        ai_recovery_enabled=enabled,
        ai_recovery_interval_seconds=1,
    )


async def test_the_sweeper_does_not_start_when_the_flag_is_off(fresh_database_url: str) -> None:
    app = create_app(lifespan_settings(fresh_database_url, enabled=False))

    async with app.router.lifespan_context(app):
        assert app.state.recovery_task is None


async def test_the_sweeper_starts_with_the_app_recovers_a_ticket_and_stops_on_shutdown(
    world: World, fresh_database_url: str
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], created_at=minutes_ago(10))
    ticket_id = ticket.id
    app = create_app(
        lifespan_settings(fresh_database_url, enabled=True),
        triage_model=ScriptedModel(good_answer(None)),
    )

    async with app.router.lifespan_context(app):
        task = app.state.recovery_task
        assert task is not None
        for _ in range(50):
            if await status_of(world, ticket_id) != AiStatus.PENDING:
                break
            await asyncio.sleep(0.1)
        assert await status_of(world, ticket_id) == AiStatus.COMPLETED

    assert task.done()
