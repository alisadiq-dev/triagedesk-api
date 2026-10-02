"""Benchmark GET /api/v1/tickets on a throwaway database with 10,000 seeded tickets.

Usage: PYTHONPATH=. BENCH_ADMIN_URL=<asyncpg URL of the local test Postgres, any database> \
       .venv/bin/python scripts/benchmark_list.py [--tickets 10000] [--requests 200]

It creates a new database, migrates it, seeds it, measures the endpoint in process (app plus
database, no network), prints EXPLAIN ANALYZE for the list queries with and without the
secondary indexes, and drops the database. Nothing else is touched. Prints Markdown.
"""

import argparse
import asyncio
import os
import re
import statistics
import time
import uuid
from collections.abc import Sequence

import httpx2
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.deps import ensure_database
from app.core.config import Settings
from app.core.security import AuthenticatedUser, AuthenticationError
from app.main import create_app

ADMIN = uuid.uuid4()
AGENT = uuid.uuid4()
CUSTOMER = uuid.uuid4()
TOKENS = {"admin": ADMIN, "agent": AGENT, "customer": CUSTOMER}

SEED_SQL = """
INSERT INTO profiles (id, email, role) VALUES (:admin, 'admin@bench.test', 'admin'),
  (:agent, 'agent@bench.test', 'agent'), (:customer, 'customer@bench.test', 'customer');
INSERT INTO profiles (id, email, role)
  SELECT gen_random_uuid(), 'agent' || n || '@bench.test', 'agent' FROM generate_series(1, 19) n;
INSERT INTO profiles (id, email, role)
  SELECT gen_random_uuid(), 'c' || n || '@bench.test', 'customer' FROM generate_series(1, 499) n;
INSERT INTO categories (name) VALUES ('Billing'), ('Technical Issue'), ('Account Access'),
  ('Feature Request'), ('General Inquiry');
INSERT INTO sla_policies (priority, response_hours, resolution_hours) VALUES
  ('urgent', 1, 4), ('high', 4, 24), ('medium', 8, 72), ('low', 24, 168);

WITH base AS (
  SELECT n,
    (ARRAY['low','medium','high','urgent'])[1 + (random()*3)::int] AS priority,
    (ARRAY['open','open','in_progress','in_progress','waiting_on_customer','resolved','closed'])
      [1 + (random()*6)::int] AS status,
    now() - (random() * interval '90 days') AS created_at,
    (ARRAY['invoice','login','printer','refund','password','outage','export','billing','api','report'])
      [1 + (random()*9)::int] AS word
  FROM generate_series(1, :n) n
), customers AS (
  SELECT id, row_number() OVER () AS rn FROM profiles WHERE role = 'customer'
), agents AS (
  SELECT id, row_number() OVER () AS rn FROM profiles WHERE role = 'agent'
)
INSERT INTO tickets (customer_id, assignee_id, title, description, category_id, category_source,
  priority, priority_source, status, ai_status, first_response_due_at, resolution_due_at,
  first_responded_at, resolved_at, created_at, updated_at)
SELECT
  (SELECT id FROM customers WHERE rn = 1 + (b.n % 500)),
  CASE WHEN b.status = 'open' AND b.n % 3 <> 0 THEN NULL
       ELSE (SELECT id FROM agents WHERE rn = 1 + (b.n % 20)) END,
  'Problem with ' || b.word || ' number ' || b.n,
  'The customer reports an issue about ' || b.word || ' and needs help soon. Reference ' || b.n,
  1 + (b.n % 5), 'ai', b.priority, 'ai', b.status, 'completed',
  b.created_at + make_interval(hours => CASE b.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 4
    WHEN 'medium' THEN 8 ELSE 24 END),
  b.created_at + make_interval(hours => CASE b.priority WHEN 'urgent' THEN 4 WHEN 'high' THEN 24
    WHEN 'medium' THEN 72 ELSE 168 END),
  CASE WHEN b.status <> 'open' THEN b.created_at + interval '30 minutes' END,
  CASE WHEN b.status IN ('resolved','closed') THEN b.created_at + interval '5 hours' END,
  b.created_at, b.created_at
FROM base b;
"""

SCENARIOS: list[tuple[str, str, str]] = [
    ("admin, default (newest first)", "admin", "page_size=20"),
    ("admin, status=open", "admin", "status=open&page_size=20"),
    ("admin, priority=urgent&category_id=2", "admin", "priority=urgent&category_id=2&page_size=20"),
    ("admin, sla_breached=true", "admin", "sla_breached=true&page_size=20"),
    ("admin, q=invoice (full text)", "admin", "q=invoice&page_size=20"),
    ("admin, sort by resolution due", "admin", "sort=resolution_due_at&page_size=20"),
    ("admin, deep page 100 of 100", "admin", "page=100&page_size=100"),
    ("agent, default (unassigned or own)", "agent", "page_size=20"),
    ("customer, default (own tickets)", "customer", "page_size=20"),
]


class BenchVerifier:
    async def verify(self, token: str) -> AuthenticatedUser:
        user_id = TOKENS.get(token)
        if user_id is None:
            raise AuthenticationError
        return AuthenticatedUser(id=user_id, email=f"{token}@bench.test")


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct))]


async def migrate(url: str) -> None:
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    await asyncio.to_thread(command.upgrade, config, "head")


async def measure(client: httpx2.AsyncClient, who: str, query: str, requests: int) -> list[float]:
    headers = {"Authorization": f"Bearer {who}", "User-Agent": "triagedesk-benchmark/1.0"}
    for _ in range(10):  # warm-up
        await client.get(f"/api/v1/tickets?{query}", headers=headers)
    timings: list[float] = []
    for _ in range(requests):
        started = time.perf_counter()
        response = await client.get(f"/api/v1/tickets?{query}", headers=headers)
        timings.append((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            raise RuntimeError(f"unexpected status {response.status_code} for {query}")
    return timings


async def explain(url: str) -> dict[str, str]:
    """EXPLAIN ANALYZE the page SELECT the list endpoint runs for each admin scenario."""
    engine = create_async_engine(url)
    captured: list[tuple[str, tuple[object, ...]]] = []

    def capture(
        _conn: object, _cursor: object, statement: str, parameters: Sequence[object], *_: object
    ) -> None:
        if "FROM tickets" in statement and "count(" not in statement.lower():
            captured.append((statement, tuple(parameters)))

    settings = Settings(
        _env_file=None,
        database_url=SecretStr(url),
        ai_recovery_enabled=False,
        rate_limit_enabled=False,  # this measures the endpoint, not the limiter
    )
    app = create_app(settings, token_verifier=BenchVerifier())
    headers = {"Authorization": "Bearer admin"}
    plans: dict[str, str] = {}
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://bench"
    ) as client:
        await client.get("/api/v1/tickets", headers=headers)  # creates the pool and the profile
        sync_engine = ensure_database(app).engine.sync_engine
        for name, who, query in SCENARIOS:
            if who != "admin":
                continue
            captured.clear()
            event.listen(sync_engine, "before_cursor_execute", capture)
            await client.get(f"/api/v1/tickets?{query}", headers=headers)
            event.remove(sync_engine, "before_cursor_execute", capture)
            if captured:
                statement, parameters = captured[-1]
                async with engine.connect() as connection:
                    result = await connection.exec_driver_sql(
                        "EXPLAIN (ANALYZE, BUFFERS) " + statement, parameters
                    )
                    plans[name] = "\n".join(row[0] for row in result)
        await ensure_database(app).dispose()
    await engine.dispose()
    return plans


async def main(tickets: int, requests: int) -> None:
    admin_url = os.environ["BENCH_ADMIN_URL"]
    name = f"bench_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        await migrate(url)
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            values = {"n": tickets, "admin": ADMIN, "agent": AGENT, "customer": CUSTOMER}
            for statement in re.split(r";\s*\n(?=INSERT|WITH)", SEED_SQL):
                if statement.strip():
                    await connection.execute(text(statement.strip().rstrip(";")), values)
        async with engine.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text("ANALYZE"))
        print(f"## Latency (in process, {tickets} tickets, {requests} requests per scenario)\n")
        print("| Scenario | p50 ms | p95 ms | p99 ms | max ms |\n|---|---|---|---|---|")
        settings = Settings(
            _env_file=None,
            database_url=SecretStr(url),
            ai_recovery_enabled=False,
            rate_limit_enabled=False,  # this measures the endpoint, not the limiter
        )
        app = create_app(settings, token_verifier=BenchVerifier())
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://bench"
        ) as client:
            for label, who, query in SCENARIOS:
                timings = await measure(client, who, query, requests)
                p50, p95, p99 = (
                    statistics.median(timings),
                    percentile(timings, 0.95),
                    percentile(timings, 0.99),
                )
                print(f"| {label} | {p50:.1f} | {p95:.1f} | {p99:.1f} | {max(timings):.1f} |")
        if app.state.database is not None:
            await app.state.database.dispose()
        print("\n## EXPLAIN ANALYZE with the indexes\n")
        for label, plan in (await explain(url)).items():
            print(f"### {label}\n```\n{plan}\n```\n")
        async with engine.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            names = await connection.execute(
                text(
                    "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE i.indrelid = 'tickets'::regclass AND NOT i.indisprimary"
                )
            )
            for (index_name,) in names.all():
                await connection.execute(text(f'DROP INDEX "{index_name}"'))
            await connection.execute(text("ANALYZE"))
        print("## EXPLAIN ANALYZE without the secondary indexes (same data)\n")
        for label, plan in (await explain(url)).items():
            print(f"### {label}\n```\n{plan}\n```\n")
        await engine.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickets", type=int, default=10_000)
    parser.add_argument("--requests", type=int, default=200)
    args = parser.parse_args()
    asyncio.run(main(args.tickets, args.requests))
