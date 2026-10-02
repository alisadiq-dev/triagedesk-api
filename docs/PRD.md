# TriageDesk API — Product Requirements Document

Status: APPROVED (2026-10-02) with the updates in sections 4, 6, 7, 9 and 19.

## 1. Objective

A portfolio-grade, production-style backend (no frontend) for AI-assisted support ticket management.
Customers create tickets. AI classifies each ticket (category, priority, sentiment) and drafts a suggested reply.
Support agents work tickets through a status workflow. Admins manage users, categories and SLA policies.

Success means: every role is permission-tested on every endpoint, AI failure never blocks ticket creation,
CI is green, and the app is deployed over HTTPS with a rollback path.

Out of scope: frontend, email ingestion, file attachments, live chat, business-hours SLA, auto-assignment,
re-triage endpoint, automatic status transitions, a separate SLA-policy audit trail, ticket deletion,
editing a ticket's title or description.

## 2. Tech stack (fixed)

Python 3.12, FastAPI, Pydantic v2, PostgreSQL (Supabase hosted), SQLAlchemy 2.0 async, Alembic,
Supabase Auth (JWT, ES256 via JWKS), Gemini API, Pytest, Ruff, mypy, pip-audit, Docker, Docker Compose,
GitHub Actions, Nginx. Exact versions are pinned in Phase 0 after checking official docs.
Any dependency beyond this list needs approval first.

## 3. Locked decisions

- No Redis, no Celery, no microservices. AI triage runs in FastAPI BackgroundTasks after ticket creation.
- Schema changes only through Alembic migrations, each with a working downgrade. After the first deploy: expand, migrate, contract.
- Repository pattern over SQLAlchemy. No ORM calls in routers.
- Permissions enforced in the service layer. No reliance on Supabase RLS.

## 4. Roles and permissions

| Role | Can |
|---|---|
| customer | Create tickets. See only own tickets. Add public comments. Never changes status. |
| agent | See unassigned tickets and tickets assigned to them. Claim and release. As assignee only: change status (including reopen), add internal notes, add public comments, override category and priority, read the AI draft. |
| admin | Everything, including commenting on any non-closed ticket, plus manage users and roles, categories, SLA policies, assignment to other agents. |

Rules:
- A customer requesting another user's ticket gets 404. An agent requesting a ticket assigned to another agent gets 404.
- The role lives only in `profiles.role`, never in JWT claims. Admin checks read the database.
- No request body from a non-admin can set or change a role (tested).
- Only an admin can change roles, through an admin endpoint. An admin cannot change an agent to customer while that agent has non-closed assigned tickets (409).
- Every role is tested on every endpoint.

## 5. Identity and profiles

- Supabase Auth handles signup and login. The backend only verifies tokens.
- JWT verification: signature, `exp`, `aud`, `iss`, pinned algorithm ES256, keys from JWKS. Not the legacy HS256 secret.
- Profile is created on the first valid authenticated request with `role=customer`, id = JWT `sub`.
  Idempotent: `INSERT ... ON CONFLICT DO NOTHING`. Tested with two concurrent first requests.
- First admin: seed script upserts a profile with `role=admin` from an env var holding the Supabase user `sub`. Safe to re-run.
- No database trigger on `auth.users`.

## 6. Status workflow

`open -> in_progress -> waiting_on_customer -> in_progress -> resolved -> closed`.
Reopen is allowed from `resolved` only, and goes to `in_progress`. Only the assignee agent or an admin can reopen;
customers never change status.
`closed` is final. Tickets are never deleted (no delete endpoint). No comments of any kind (public or internal,
by anyone) can be added to a closed ticket (409).
Invalid transitions return 409 with a clear message. All transitions are manual: a customer comment never
changes status. Every change writes a `ticket_events` row in the same transaction.
Every valid and invalid transition is tested.

## 7. Assignment

- An agent can claim an unassigned ticket and release their own ticket. Concurrent claims are resolved with a conditional update; the loser gets 409.
- Only an admin can assign to another agent or reassign. Assignees must have role agent or admin.
- A closed ticket cannot be claimed or assigned (409).
- Release keeps the status, clears the assignee and writes a `ticket_events` row.
- Only the assignee or an admin can change status, add internal notes, add public comments, or override category and priority.
  A non-assignee agent cannot see the ticket at all (404), so this is enforced by visibility plus a service-layer check.
- No auto-assignment. Assignment changes write `ticket_events`.

## 8. SLA

- 24/7 wall-clock hours. Policy: one `sla_policies` row per fixed priority (low, medium, high, urgent), with `response_hours` and `resolution_hours`. Never deleted. Columns `updated_at` and `updated_by`; no separate audit trail.
- Ticket columns: `first_response_due_at`, `resolution_due_at`, `first_responded_at`, `resolved_at`. Indexes on both due columns.
- At creation the deadlines use the default priority `medium`. After triage (or keyword fallback) or a human override, both deadlines are recalculated from `created_at` (never from the time of the change).
- First response = a public comment by an agent or admin. Customer comments and internal notes do not count.
- Reopen clears `resolved_at`; the old value stays in `ticket_events`. `resolution_due_at` is unchanged.
- Breach = (`now() > first_response_due_at` and `first_responded_at` is null) or (`now() > resolution_due_at` and `resolved_at` is null).
  Implemented as a SQL expression plus a Python property over the same logic, so breached tickets can be filtered and paginated in the database. No stored flag, no cron job.
- Policy edits apply only to deadlines calculated afterwards. Existing due dates are never rewritten.

Known limitations: the clock does not pause in `waiting_on_customer`. A reopened ticket keeps its original
`resolution_due_at` and may show as breached immediately.

## 9. Categories and priorities

- Customer sends only title and description. No category or priority input.
- After creation, nobody edits a ticket's title or description. A customer adds new information as a public comment.
  The only ticket fields that change after creation are status, assignee, and the category and priority overrides below.
- Categories: admin-managed, unique names, `is_active` flag, soft-deactivate only, no hard delete.
  Inactive categories leave the triage prompt list and new assignments; existing tickets keep them.
- Priority: fixed enum of four values, not admin-managed.
- Agents and admins (assignee or admin) can override category and priority. Each override writes `ticket_events`.
  The system stores whether a field was set by AI or by a human. Triage never overwrites a human override, even if triage finishes later.

## 10. AI triage

- Structured output validated by Pydantic. Category must be one of the active category ids. Unknown values are treated as invalid data.
- Failure, timeout or invalid data: priority from simple keyword rules, `ai_status = failed`, category and sentiment stay null (no default category), and ticket creation is never blocked.
- Store model name and prompt version on each triage result.
- Ticket text is untrusted input (prompt injection). AI output is only data: it can never change roles, permissions or ticket ownership.
- The suggested reply is stored text only. An agent edits it and posts it as a normal public comment. The AI never posts comments or sends anything.
- No automatic re-run and no re-triage endpoint.
- Known gap: BackgroundTasks lose work on restart, so tickets can stay `ai_status = pending`. A fix will be proposed in Phase 5 and built only after approval.
- Gemini is mocked in all tests. The SDK API is checked against current official docs before use.

## 11. Customer response schema

A separate response schema built from an allowlist. Customers see only: `id`, `title`, `description`, `status`,
`created_at`, `updated_at`, and public comments. Each public comment shows `author_type` ("customer" or "support")
and `created_at`, and never the agent's name, email or id.
Customers never see category, priority, sentiment, `ai_status`, the suggested reply, SLA fields, the breach flag,
the assignee, internal notes, or audit history.
A test asserts the customer schema contains exactly the allowlisted fields.
Responses for all roles never expose internal database fields.

## 12. API conventions (details approved separately before endpoint code)

- Base path `/api/v1`. Error format `{"error": {"code", "message"}}`.
- Status codes: 400, 401, 403, 404 (not found or not yours), 409 (invalid state), 422, 429.
- One pagination shape for all list endpoints (proposed in the API contract).
- Rate limiting on public routes. `/health` is liveness, `/ready` checks the database.

## 13. Data model (proposed and approved separately before the first migration)

`profiles`, `categories`, `sla_policies`, `tickets`, `ticket_comments` (`is_internal`), `ticket_events` (who, what, from, to, when).
Indexes: status, assignee_id, created_at, both SLA due columns, full-text search on title and description.

## 14. Commands (created in Phase 0)

`make run`, `make test`, `make lint` (ruff + mypy), `make migrate`, `make seed`, `make audit` (pip-audit).

## 15. Project structure

```
app/api/routers  app/schemas  app/models  app/repositories  app/services
app/ai (triage.py, prompts.py, behind an interface)
app/core (config, db session, logging, security, errors)
alembic/  deploy/ (nginx.conf, docker-compose.prod.yml)
tests/unit  tests/integration  docs/  CLAUDE.md  CONSTRAINTS.md  tasks.md
```

Routers stay thin. All config comes from env vars.

## 16. Code style

Ruff clean, mypy clean, full type hints, small functions, descriptive names. No `noqa`, `type: ignore`, skip or xfail without approval.

## 17. Testing

TDD: failing test first. Pyramid about 80% unit, 15% integration, 5% end to end. Integration tests run against real PostgreSQL in Docker.
Descriptive DAMP test names. Every bug fix starts with a failing test. Coverage at least 85% on `app/services` and `app/ai`.
Tests include: every role on every endpoint, expired, missing and wrong-audience tokens, concurrent profile creation, concurrent claim,
customer schema allowlist, role-change protection, prompt-injection cases.

## 18. Working rules

Always: failing test first, full suite before each commit, main stays green, config from env vars, update `docs/progress.md` each phase.
Ask first: new dependencies, schema changes after Phase 1, changing a locked decision, any destructive command, anything touching the production server.
Never: commit secrets or `.env`, log tokens or secrets, delete or weaken a test, add suppressions without approval, lower thresholds in CONSTRAINTS.md,
let the LLM change roles, permissions or ticket ownership.

Process rules from earlier projects:
- Secrets live in `~/.secrets/triagedesk-api.env` (chmod 600). Source it only inside the command that needs it. Never print or echo values; check by length only. Never ask for secrets in chat.
- Supabase project: Data API off, "automatically expose new tables" off, ES256 JWT signing keys (JWKS), connection through the session pooler (IPv4), not the direct host.
- Where third-party behaviour matters, run a live check against the real service and state exactly what is unverified. Do not stop on a guess.
- A task is done only when CI is green on GitHub, not just locally.
- When a PR is opened, stop and say so. The owner merges after that.
- Scripts that call the deployed API send a normal User-Agent.

## 19. Open items (not decided yet)

- Deployment target: DigitalOcean droplet or Azure for Students Ubuntu VM. Ask before Phase 9; do not assume.
- Pagination shape and endpoint list: in the API contract (Phase 3).
- Data model and first migration: proposed in Phase 1.
- Rate limit numbers: Phase 8. List endpoint p95 latency target: Phase 7, after first measurement.
- Fix for stuck `ai_status = pending`: proposed in Phase 5.
