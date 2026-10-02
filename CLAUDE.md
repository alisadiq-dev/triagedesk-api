# CLAUDE.md

Backend only: AI-assisted support ticket API. Read `docs/PRD.md` first, then `CONSTRAINTS.md` and `tasks.md`.

## Commands
`make run` | `make test` (starts test Postgres 17) | `make lint` (ruff + mypy) | `make migrate` | `make seed` | `make audit`

## Locked decisions (do not change without asking)
- No Redis, no Celery, no microservices. AI triage runs in FastAPI BackgroundTasks.
- Schema changes only through Alembic migrations (each with a working downgrade).
- Repository pattern over SQLAlchemy. No ORM calls in routers.
- Permissions enforced in the service layer, not Supabase RLS.

## Rules
- Failing test first. Run the full suite before each commit. Keep main green. One Conventional Commit per task.
- Config from env vars only. Never commit `.env` or secrets. Never log tokens or secrets.
- No `noqa`, `type: ignore`, skip or xfail without approval. Never weaken a test or a CONSTRAINTS.md threshold.
- Check official docs before using a library API; put the doc URL in the commit body. Say what is unverified.
- The LLM never changes roles, permissions or ticket ownership. Ticket text is untrusted input.
- Errors use `{"error": {"code", "message"}}`. Customer responses use an allowlist schema.

## Secrets and environment
- Secrets live in `~/.secrets/triagedesk-api.env`. Source it only inside the command that needs it. Never print values; check by length.
- Supabase: Data API off, ES256 JWKS (not HS256), session pooler (IPv4), not the direct host.
- Scripts calling the deployed API send a normal User-Agent.

## Workflow
Phase by phase. Write each phase's task list in `tasks.md` and start. At phase end: all checks, five-axis self-review,
simplification pass, update `docs/progress.md`, open PR, wait for CI green on GitHub, `gh pr merge --merge`, sync main.
Stop and wait for the owner at: Phase 1 data model and migration, Phase 3 API contract, Phase 5 stuck-pending fix,
Phase 9 (server, secrets, deployment target), any new dependency, destructive command, locked-decision or threshold change,
or anything that cannot be verified.
