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
- Plan change (2026-10-02): the database is the local Docker Postgres 17, so no Supabase pooler check is needed.
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

## Plan change (2026-10-02): local only
- No cloud deployment and no hosted Supabase project (ADR 0005). Auth tokens come from the local Supabase stack (`supabase start`, ES256 signing keys); tests use a local test key and a faked JWKS.
- The app database is the local Docker Postgres 17 for development, tests and the local production setup.
- Phase 9 becomes "local production setup": `docker-compose.prod.yml` (app, Nginx reverse proxy, Postgres), non-root containers, `/ready` and a smoke test against the Nginx URL, `docs/runbook.md` for start, stop, backup and restore. No SSH deploy job, no server hardening, no Let's Encrypt.

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

Data model approved 2026-10-02 (see `docs/data-model.md`). Carried to Phase 3: the role-change endpoint writes one structured log line (actor, target, old role, new role, request id), with a test.

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

### Phase 1 close (done)
All checks, five-axis review, simplification pass, update `docs/progress.md`, open PR, wait for CI green, merge, sync main.

## Phase 2: Auth and RBAC

New dependency approved by the owner: `PyJWT[crypto]==2.15.1` (httpx2 moves to runtime for the JWKS client).

- [x] P2.1 Profile creation on first valid request (idempotent, concurrent-safe), role read from the database only.
- [x] P2.2 `Actor`, `require_role`, 401/403 error types, `get_actor` and `require_roles` dependencies; tests for customer, agent and admin.
- [x] P2.3 `JwksProvider` (timeout, cache, rate-limited refetch, failure backoff, only public ES256 keys) with tests.
- [x] P2.4 `JwtTokenVerifier` (ES256 pinned, required claims, aud and iss from settings, is_anonymous rejected, identical 401 body, 503 fail-closed with a log line) with tests for expired, missing, wrong-audience, wrong-issuer, wrong-algorithm, tampered and unsigned tokens.
- [x] P2.5 Missing token gets 401 before any JWKS or database work (tested).
- [x] P2.6 Local Supabase stack: git-ignored `supabase/signing_keys.json` with an ES256 key, `docs/local-supabase.md`, live check script confirming `aud`, `iss`, ES256 and verification of a real token.
- [x] P2.7 Security audit (security-auditor persona) and fixes, then phase close: checks, five-axis review, progress, PR, CI green, merge.

## Phase 3: Tickets, comments, users, categories, SLA policies (contract: docs/api-contract.md, approved)

Builds endpoints 1 to 13, 15 to 17, 20 and 21. TDD per slice; permissions in the service layer; routers stay thin.

- [x] P3.1 Request id middleware, JSON log formatter, 400 for malformed JSON, `Page[T]` and page params, `/api/v1` router, `GET /me`.
- [x] P3.2 Users: list, get, change role (own-role and open-assigned-tickets guards, one structured log line with actor, target, old and new role, request id).
- [x] P3.3 Categories (list, create, patch, soft-deactivate) and SLA policies (list, patch with merge validation).
- [x] P3.4 Tickets: create (SLA deadlines from the medium policy, `ticket_created` event), get, list (page, sort, status), CustomerTicket allowlist test.
- [x] P3.5 Ticket overrides (category, priority with SLA recalculation from `created_at`), claim (atomic), release, admin assign; events written.
- [x] P3.6 Comments: list and create with role-specific views, internal notes, first-response timestamp, closed-ticket guard.
- [x] P3.7 Permission matrix: every role on every route, plus a meta-test that fails if a route is missing from the matrix.
- [x] P3.8 Security review (security-auditor persona), fixes, phase close: checks, security review of RBAC (doubt pass), five-axis review, progress, PR, CI green, merge.

## Phase 4: Status workflow and audit log (endpoints 14 and 18)

- [x] P4.1 Transition table as a pure function with every valid and invalid pair tested.
- [x] P4.2 `POST /tickets/{id}/status`: permissions, `resolved_at` set and cleared (reopen), event rows in the same transaction.
- [x] P4.3 `GET /tickets/{id}/events` for staff, oldest first, paginated.
- [x] P4.4 Permission matrix extended; phase close as above.

## Phase 5: AI triage and suggested reply (behind an interface, mocked in all tests)

Stop points inside this phase: the Gemini SDK is a new dependency (owner approval), and the fix for stuck `ai_status = pending` is
proposed but not built until approved.

- [x] P5.1 Keyword fallback priority rules (pure function, tested).
- [x] P5.2 Prompt builder (`app/ai/prompts.py`, versioned): ticket text is delimited untrusted data with a per-call boundary; tests for delimiter-forging and truncation.
- [x] P5.3 Output validation (Pydantic, extra fields forbidden, category must be an active id): valid, extra keys, bad enums, bad types, too long, not JSON.
- [x] P5.4 `TriageModel` interface and `TriageRunner`: own sessions (no DB connection held during the model call), timeout, success path, fallback path (keyword priority, `ai_status = failed`, category and sentiment stay null), model name and prompt version stored, human overrides never overwritten, SLA recalculated from `created_at`, skip when not pending, one structured log line per run (outcome, latency, model).
- [x] P5.5 Wire into ticket creation with FastAPI BackgroundTasks; default model is a disabled one (fallback) until a real adapter is approved; staff see AI fields, customers never do.
- [x] P5.6 Prompt-injection tests: hostile ticket text and hostile model output can only ever change the allowed AI fields, never roles, assignee, status or permissions.
- [x] P5.7 Gemini adapter (`google-genai==2.27.0`, approved 2026-10-02; `GEMINI_MODEL` default `gemini-3.5-flash-lite`), checked against the SDK docs, one live call with a fake ticket.
- [x] P5.8 Stuck-pending fix proposed in docs/adr/0006-recover-stuck-pending-triage.md (accepted 2026-10-02).
- [x] P5.9 Recovery sweeper built as accepted: startup plus every 60 s, pending older than 120 s, max 10 per run, advisory lock, env flag and numbers, one log line per run.

## Phase 6: SLA status (endpoint 19)

The breach rule (SQL expression and Python property that agree) and policy handling were built in Phases 1, 3 and 5.
- [x] P6.1 `GET /tickets/{id}/sla` (A visible, X; customer 403 on own, 404 on another's) returning `SlaStatus`; per-clock breach flags from the same rule as `sla_breached`; matrix extended; tests for in-time, missed first response, response given, missed resolution, resolved late, reopened, comment-driven first response, closed ticket.
- [x] P6.2 Phase close: checks, five-axis review, progress, PR, CI green, merge.

## Phase 7: List filters, N+1 guard, performance baseline

Contract (endpoint 11) already lists the filters. Decisions I made inside it (recorded in `docs/api-contract.md`):
customers get 403 on staff-only filters (a priority or category filter would reveal fields they must not see); `assignee_id` is
admin-only (403 otherwise); `unassigned` with `assignee_id` is 422; `created_after` is inclusive and `created_before` exclusive,
both must carry a timezone (422 otherwise); `q` is a full-text search over title and description (`plainto_tsquery`, 1 to 200 characters).

- [x] P7.1 Filters `priority`, `category_id`, `assignee_id` (X), `unassigned`, `sla_breached` (SQL expression), `q`, `created_after`, `created_before`, with tests for each, combinations, role rules, totals and pagination.
- [x] P7.2 Query-count test (constraint 8): the list endpoints run the same number of queries for 1 row and for 30 rows, for every role and with filters on.
- [x] P7.3 Benchmark script on a throwaway database with 10,000 tickets: p50/p95/p99 for the list endpoint, `EXPLAIN ANALYZE` with and without indexes, recorded in `docs/performance.md`.
- [x] P7.4 Constraint 10 number proposed from the measurement (50 ms p95; owner confirms, CONSTRAINTS.md stays TBD until then), phase close.

## Phase 8: Hardening, OWASP API Top 10 review, rate limiting, pip-audit

No Redis (locked), so rate limits are in process (per app instance) and reset on restart. No new dependency.
Rate-limit numbers are env settings with defaults (PRD: numbers are decided in this phase).

- [x] P8.1 Rate limiter (fixed window, bounded memory, injectable clock): per client IP on the public routes (`/health`, `/ready`) and on all `/api/v1` requests before any token or database work; per authenticated user on ticket creation (each ticket costs a paid model call) and on comment creation. 429 in the shared error format with `Retry-After`; an env flag turns it off.
- [x] P8.2 Request body size limit (413, also for chunked bodies), response security headers (`nosniff`, `no-store`, `frame-ancestors 'none'`; not on the interactive docs), env flag for the interactive docs and OpenAPI.
- [x] P8.3 OWASP API Security Top 10 (2023) review: independent pass by the security-auditor persona, findings fixed or accepted, written up in `docs/security-review-owasp-api.md` with the test that backs each control.
- [x] P8.4 `make audit` clean and CI audit step confirmed; phase close: checks, five-axis review, progress, PR, CI green, merge.

## Phase 9: Local production setup (approved by the owner 2026-10-02; ADR 0005: no TLS, no cloud)

Decisions from the owner: signups off on the local Supabase stack with admin-API demo users; exactly one uvicorn worker, with the
per-instance limits documented in the README; audit item L4 accepted and documented; uvicorn access log off, our structured access log
with the route template only; Nginx overwrites `X-Forwarded-For`, `client_max_body_size 64k`, docs not exposed; Postgres on a named volume.

- [x] P9.0 Done before the compose work: `AI_MAX_OUTPUT_TOKENS` (one more live call, verified), structured access log with a test, `SUPABASE_JWKS_BASE_URL`, signups off, `scripts/create_demo_users.py` with tests.
- [x] P9.1 `docker-compose.prod.yml` (db with a named volume and no published port, one-shot migrate, app with 1 worker and `--proxy-headers --forwarded-allow-ips` set to the Nginx container's fixed address only, read-only root, non-root, Nginx unprivileged on 127.0.0.1) and `deploy/nginx.conf`; `deploy/prod.env.example`, `deploy/init_secrets.sh`; `make prod-*` targets; the image carries Alembic.
- [x] P9.2 `scripts/smoke_test.py` against the Nginx URL (health, ready, docs hidden, 401 shape, 413, security headers, spoofed `X-Forwarded-For` does not bypass the rate limit, optional signed-in flow with the demo users); unit tests for its checks; run for real against a throwaway project.
- [x] P9.3 `docs/runbook.md`: start, stop, seed, demo users, backup and restore with `pg_dump` and `pg_restore`; the restore procedure is run for real against the throwaway project.
- [x] P9.4 CI checks the compose file and the Nginx config; phase close: checks, five-axis review, progress, PR, CI green, merge.

## Phase 10: Documentation and demo

- [x] P10.1 README (what it is, architecture, quick start, commands, per-instance rate limit note, known limitations including L4), `docs/architecture.md` with Mermaid diagrams (context, triage sequence, status workflow, deployment).
- [x] P10.2 ADRs 0007 to 0010 for the decisions of Phases 5 to 9 that have none yet.
- [x] P10.3 `docs/curl-collection.md` and `scripts/demo.py` (an end-to-end demo with the demo users), run against the local production stack.
- [x] P10.4 Phase close: checks, review, progress, PR, CI green, merge, final summary.
