# TriageDesk API

A backend for AI-assisted support tickets. Customers open tickets; an LLM classifies each one (category, priority, sentiment) and drafts
a reply for a human; agents work tickets through a status workflow with SLA tracking; admins manage users, categories and SLA policies.
No frontend. It is a portfolio-grade project built to be run, tested and reviewed as a real service: every role is permission-tested on
every endpoint, AI failure never blocks a ticket, and the whole thing runs locally in a production-style Docker Compose setup.

[![CI](https://github.com/alisadiq-dev/triagedesk-api/actions/workflows/ci.yml/badge.svg)](https://github.com/alisadiq-dev/triagedesk-api/actions/workflows/ci.yml)

## Highlights

- **Roles done properly.** `customer`, `agent`, `admin`. The role lives only in the database, never in a token. A ticket you may not see
  is a 404. Customers get separate allowlist response schemas. A matrix test runs every role against every route and fails if a route
  is missing from it.
- **AI that cannot hurt.** Ticket text is untrusted input. The model is behind an interface, its output is validated strictly, it can only
  write the AI fields (never over a human override), and any failure falls back to keyword rules without blocking ticket creation.
  Lost background work is recovered by a small sweeper. See `docs/adr/0008-ai-trust-boundary-and-cost-controls.md`.
- **SLA as data.** 24/7 deadlines per priority, a breach rule implemented once as a SQL expression and once in Python (tested to agree),
  an SLA status endpoint and a `sla_breached` filter.
- **Audit trail.** Every status, assignment and override change writes a `ticket_events` row in the same transaction.
- **Hardened.** ES256 token verification, in-process rate limits, body size limit, security headers, an OWASP API Top 10 review
  (`docs/security-review-owasp-api.md`), pip-audit in CI.
- **Measured.** A query-count test guards against N+1; the list endpoint has a p95 target and a benchmark (`docs/performance.md`).

## Stack

Python 3.12, FastAPI, Pydantic v2, PostgreSQL 17, SQLAlchemy 2 (async) with the repository pattern, Alembic, Supabase Auth (local stack,
ES256 via JWKS) as token issuer, Gemini (`google-genai`), pytest, Ruff, mypy (strict), pip-audit, Docker Compose, Nginx, GitHub Actions.
No Redis, no Celery, no microservices (ADR 0001).

Architecture diagrams: `docs/architecture.md`. Data model and ER diagram: `docs/data-model.md`.

## Quick start (development)

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
make test        # starts a throwaway Postgres 17 in Docker, runs the suite with the coverage gate
make lint        # ruff + mypy
make audit       # pip-audit
```

Run the API against a database of your own:

```bash
cp .env.example .env            # then set DATABASE_URL (never commit .env)
make migrate && make seed
make run                        # http://127.0.0.1:8000, interactive docs at /docs (development only)
```

Sign-in needs tokens from the local Supabase stack (`docs/local-supabase.md`). Signups are off there; create demo users with
`scripts/create_demo_users.py`.

## Run it like production, locally

```bash
sh deploy/init_secrets.sh       # once: ~/.secrets/triagedesk-prod.env with a random database password
make prod-up                    # Postgres + migrations + app + Nginx on http://127.0.0.1:8080
make prod-seed
make smoke                      # checks through Nginx
make prod-backup                # pg_dump; restore with RESTORE_CONFIRM=yes make prod-restore FILE=...
```

Full procedure, backup and restore: `docs/runbook.md`. No TLS and no cloud deployment (ADR 0005).

## Try it

```bash
eval "$(supabase status -o env | sed 's/^/export SUPABASE_/')"
.venv/bin/python -m scripts.create_demo_users            # customer, agent, admin
PYTHONPATH=. .venv/bin/python -m scripts.demo            # one ticket through its whole life, printing what each role sees
```

Or call the endpoints yourself: `docs/curl-collection.md`.

## API at a glance

Base path `/api/v1`, JSON, `snake_case`, errors as `{"error": {"code", "message"}}`, lists as `{items, page, page_size, total}`.
Full contract: `docs/api-contract.md`. C = customer, A = agent (as assignee where it changes a ticket), X = admin.

| Area | Endpoints | Roles |
|---|---|---|
| Identity | `GET /me` | C A X |
| Users | `GET /users`, `GET /users/{id}`, `PATCH /users/{id}` (role) | X |
| Categories | `GET /categories`, `POST /categories`, `PATCH /categories/{id}` (soft deactivate) | A X (write: X) |
| SLA policies | `GET /sla-policies`, `PATCH /sla-policies/{priority}` | A X (write: X) |
| Tickets | `POST /tickets` (C), `GET /tickets` (filters, search, sort), `GET /tickets/{id}` | C A X |
| Ticket work | `PATCH /tickets/{id}` (category, priority), `POST .../status`, `POST .../claim`, `POST .../release`, `PUT .../assignee` | A X |
| Insight | `GET /tickets/{id}/events` (audit), `GET /tickets/{id}/sla` | A X |
| Comments | `GET` and `POST /tickets/{id}/comments` (internal notes for staff) | C A X |

`/health` (liveness) and `/ready` (database) are public and outside `/api/v1`.

## Rate limits and the one-worker rule

Rate limits are held in the app process: **per instance, with no shared store, reset on restart**. That is why the production compose
file runs exactly **one uvicorn worker**. Do not scale the app to more workers or instances without replacing the limiter (ADR 0007).

| What | Key | Default |
|---|---|---|
| `/health` and `/ready` | client address | 120 per minute |
| everything under `/api/v1` | client address | 600 per minute |
| ticket creation | user | 10 per minute |
| comment creation | user | 30 per minute |
| model calls | global | 60 per minute (then the keyword fallback) |

Every number is an env setting (`.env.example`), and `RATE_LIMIT_ENABLED=false` turns the limiter off. A refused request is `429` with
`Retry-After`. Request bodies are limited to 64 KiB (`413`).

## Quality gates (CONSTRAINTS.md)

Ruff and mypy strict clean; at least 85% coverage on `app/services` and `app/ai` (about 96% now); no `noqa`, `type: ignore`, skip or xfail
without approval; no secrets in git; pip-audit clean; a permission test for every role on every endpoint; constant query count on list
endpoints; migrations apply on a fresh Postgres and every migration has a working downgrade; list p95 of 50 ms or less (measured with
`make bench`). CI runs all of it on every push and also validates the production compose file and the Nginx config.

## Known limitations

- **Prompt injection can raise a ticket's AI priority** (security review L4). A hostile ticket can talk the model into "urgent", which
  shortens that ticket's SLA and moves it up the queue. It cannot change roles, permissions, ownership or status. Staff see the priority
  source and can override it (ADR 0008).
- Rate limits are per instance and reset on restart (above). On Docker Desktop all local clients share one rate-limit address.
- The SLA clock does not pause while waiting on the customer, and a reopened ticket keeps its original resolution deadline.
- No re-triage endpoint: a ticket that took the fallback stays that way unless a human sets category and priority.
- Offset pagination; very deep pages are the slowest list queries (`docs/performance.md`).
- Retrying `POST /tickets` or a comment after a timeout can create a duplicate (no idempotency keys).
- Local only: no TLS, no cloud (ADR 0005). `docs/adr/0005-defer-cloud-deployment.md` lists what a deployment would need.

## Repository map

```
app/api         routers (thin), dependencies, /api/v1 wiring     app/services   permissions, visibility, workflow, SLA
app/repositories  all SQL                                         app/ai         prompt, interface, validation, Gemini, recovery
app/core        config, db, auth, errors, logging, hardening      app/models, app/schemas
alembic/        migrations (each with a downgrade)                tests/         unit, integration (real Postgres), support
deploy/         nginx.conf, secrets, backup, restore              docker-compose.yml (tests), docker-compose.prod.yml
scripts/        demo, demo users, smoke test, benchmark           docs/          PRD, contract, data model, ADRs, runbook, reviews
```

Start with `docs/PRD.md`, then `CONSTRAINTS.md` and `tasks.md`; `docs/progress.md` records every phase with its checks, reviews and what was
not verified. ADRs are in `docs/adr/`.
