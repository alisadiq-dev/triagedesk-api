# tasks.md

Status: Phase 0 done (T0.1 to T0.9). Task lists no longer need approval. Each task is a thin vertical slice: failing test, implement, verify, commit.
One commit per task, Conventional Commits, about 100 lines per change. Work happens on branch `phase-0-setup`.
A task is done only when CI is green on GitHub (from T0.9 on; before that, local checks only, and the phase is not done until CI is green).

## Phase 0: Repo setup and tooling

### T0.1 Repo skeleton and pinned tooling
- Do: `git init`, `.gitignore` (includes `.env`, `.venv`, caches), `pyproject.toml` (ruff, mypy strict, pytest, coverage config), pinned requirements files (runtime and dev), `app/` package, one smoke test.
- Versions are pinned exactly after checking PyPI and official docs (not from memory).
- Acceptance: venv installs from pins; `pytest` passes; `ruff check` and `mypy` clean; `.env` is git-ignored (verified with `git check-ignore`).
- Depends on: approval of the dependency list (see Open questions).
- Verify: `python -m pytest && ruff check . && mypy app`

### T0.2 Config from environment variables
- Do: `app/core/config.py` (pydantic-settings), required settings fail fast with a clear message, secrets never appear in `repr` or logs.
- Acceptance (tests first): missing required var raises; values load from env; secret fields are masked in `repr`; no default holds a real secret.
- Depends on: T0.1
- Verify: `pytest tests/unit/test_config.py`

### T0.3 `/health` liveness endpoint
- Do: FastAPI app factory in `app/main.py`, `GET /health` returns 200 and does not touch the DB. Shared error format `{"error": {"code", "message"}}` for unhandled and HTTP errors (`app/core/errors.py`).
- Acceptance: test for 200 body; test that an unknown route returns the error format with 404.
- Depends on: T0.2
- Verify: `pytest tests/unit/test_health.py`

### T0.4 DB session and `/ready`
- Do: `app/core/db.py` async engine and session with an explicit pool config (size, overflow, timeout, pre-ping, recycle), configurable by env. `GET /ready` runs `SELECT 1`; returns 200, or 503 in the error format when the DB is unreachable.
- Acceptance: integration test against real PostgreSQL in Docker: 200 when up; 503 when the DB URL is wrong; pool settings are applied (asserted on the engine).
- Source check: SQLAlchemy async and asyncpg docs read first; doc URL in the commit body.
- Live check (later, in a separate approved step): behaviour through the Supabase session pooler. Not verified in Phase 0 unless you provide a Supabase project; I will state exactly what is unverified.
- Depends on: T0.3, T0.6 (test Postgres)
- Verify: `docker compose up -d test-db && pytest tests/integration/test_ready.py`

### T0.5 Makefile commands
- Do: `make run`, `test`, `lint` (ruff + mypy), `migrate`, `seed`, `audit` (pip-audit).
- `migrate` and `seed` are honest stubs in Phase 0 (print a clear "not available until Phase 1" and exit non-zero? see Open questions) and become real in Phase 1.
- Acceptance: each target runs; `make lint` and `make test` pass; `make audit` reports clean or lists findings I bring to you.
- Depends on: T0.1
- Verify: `make lint test audit`

### T0.6 Dockerfile and docker-compose
- Do: multi-stage Dockerfile, non-root user, no secrets baked in, `.dockerignore`. `docker-compose.yml` with `app` and `test-db` (Postgres, tmpfs, local-only port, healthcheck). Postgres major version is pinned.
- Acceptance: `docker compose build` succeeds; container runs as non-root (checked with `id`); `/health` answers from the container; `test-db` becomes healthy.
- Depends on: T0.3
- Verify: `docker compose up -d --build && curl -s localhost:8000/health`

### T0.7 pre-commit
- Do: `.pre-commit-config.yaml` with ruff (check and format), mypy, private-key and large-file checks, and a local hook that blocks `.env` files and new `noqa` / `type: ignore` / skip / xfail strings (see CONSTRAINTS.md).
- Acceptance: a deliberately bad commit (secret-like file, `noqa`) is rejected locally, then reverted.
- Depends on: T0.1, T0.8 (constraints approved)
- Verify: `pre-commit run --all-files`

### T0.8 Context files, `.env.example`, ADRs
- Do: `CLAUDE.md` (rules, commands, locked decisions, process rules from the PRD), `CONSTRAINTS.md` (final after your approval), `.env.example` (names only, no values), `docs/progress.md`, `docs/adr/` with short ADRs for the four locked decisions.
- Acceptance: `.env.example` lists every variable that `config.py` reads (a test compares them); no real values anywhere; `CLAUDE.md` stays short.
- Depends on: T0.2
- Verify: `pytest tests/unit/test_env_example.py`

### T0.9 GitHub repo and CI
- Do: `.github/workflows/ci.yml` on every push: ruff, mypy, pytest with coverage, pip-audit. Postgres service for integration tests. (`alembic upgrade head` joins CI in Phase 1 when Alembic exists.)
- Acceptance: CI is green on GitHub for the branch. I open the PR and then STOP so you can merge.
- Depends on: T0.1 to T0.8, and a GitHub remote (see Open questions)
- Verify: `gh run list --branch phase-0-setup` shows success

### Phase 0 close
Full tests and checks, five-axis self-review with severity labels, one simplification pass, update `docs/progress.md`, short summary and file list, then STOP for your review.

## Answers recorded (2026-10-02)

1. Dependencies: all seven approved. Test client: check the current Starlette/FastAPI docs and use what they recommend (owner mentions `httpx2`; unverified, to be checked in T0.1); note the choice and doc URL in the commit body.
2. `make migrate` and `make seed` print "available from Phase 1" and exit 0.
3. GitHub: repo `triagedesk-api`, public, `gh` authenticated as `alisadiq-dev`. Create the remote with `gh repo create` after the first commit.
4. Test Postgres: PostgreSQL 17. Supabase project not created yet; the owner will say when it is ready for the live pooler check.
5. Gate: no work on T0.1 until the owner has reviewed tasks.md and CONSTRAINTS.md.

## Original questions (answered above)

1. Dependencies beyond your named stack that I need (ask-first rule). Proposed, with reason:
   `uvicorn` (ASGI server), `pydantic-settings` (env config), `asyncpg` (async Postgres driver), `httpx` (test client), `pytest-asyncio`, `pytest-cov` (coverage gate), `pre-commit`.
   Later phases: a JWT library and Gemini SDK (asked about in Phases 2 and 5). Approve, trim or change?
2. `make migrate` and `make seed` in Phase 0: should they be stubs that print "available from Phase 1" and exit 0 (so `make` chains keep working), or exit non-zero until real? I recommend exit 0 with a clear message.
3. GitHub: which repository name and visibility, and is `gh` already authenticated on this machine? I need a remote for CI to run.
4. Postgres major version for the test container: I'd match the Supabase major version. I'll check the version of your Supabase project by length-safe means only if you give me access; otherwise I pick the current stable and flag it.

## Phase 1: Data model, Alembic, first migration, seed

CHECKPOINT: the proposal in `docs/data-model.md` must be approved by the owner before any model or migration code is written.
Tasks below start only after that approval.

### P1.1 Alembic async setup
- Do: `alembic init -t async` (checked against current Alembic docs), `env.py` reads the DB URL from Settings, naming convention for constraints and indexes.
- Acceptance: `make migrate` runs `alembic upgrade head` against the test DB with zero revisions and exits 0.
- Verify: `make migrate` (with DATABASE_URL pointing at the test DB)

### P1.2 Enums, profiles, categories, sla_policies (models + tests)
- Acceptance (integration tests first): CHECK constraints reject bad values (unknown role, non-positive hours, resolution < response); unique case-insensitive category names.
- Depends on: P1.1

### P1.3 tickets model with SLA columns and breach expression
- Acceptance: breach SQL expression and Python property agree on a table of cases (no response, responded, resolved, reopened, not yet due); `resolved_at` invariant CHECK holds.
- Depends on: P1.2

### P1.4 ticket_comments and ticket_events models
- Acceptance: a customer cannot have `is_internal = true` (DB CHECK); events keep who, what, from, to, when.
- Depends on: P1.3

### P1.5 Migration 0001 with downgrade
- Acceptance: on a fresh Postgres, `upgrade head`, then `downgrade base`, then `upgrade head` all succeed; the resulting schema equals the models (Alembic autogenerate shows no diff).
- Self-review (doubt-driven) before commit: claim, doubt, what could break, reconcile.
- Depends on: P1.2 to P1.4

### P1.6 Indexes and full-text search
- Acceptance: tests assert each index exists (status, assignee_id, created_at, customer_id, both partial SLA indexes, GIN on search_vector) and that a `to_tsquery` search finds a ticket by title and by description words.
- Depends on: P1.5

### P1.7 Seed script and `make seed`
- Acceptance: idempotent (run twice, same rows); seeds categories and four SLA policies without overwriting admin edits; upserts the bootstrap admin profile from an env var holding the Supabase `sub`; `make migrate` and `make seed` are real.
- Depends on: P1.5

### P1.8 CI runs `alembic upgrade head` on a fresh Postgres
- Acceptance: CI green on GitHub with the new step.
- Depends on: P1.5

### Phase 1 close
All checks, five-axis review, simplification pass, update `docs/progress.md`, open PR, wait for CI green, merge, sync main.
