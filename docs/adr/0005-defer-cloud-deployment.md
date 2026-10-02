# ADR 0005: Defer cloud deployment; run locally in a production-style setup

Status: accepted (2026-10-02)

Context: Most cloud hosts need a payment card, which the owner does not have, and time is limited.
Decision: No cloud deployment and no hosted Supabase project for now. Everything runs on the owner's machine:
- Database: local Docker Postgres 17 (development, tests and the local production setup).
- Auth: the local Supabase stack (`supabase start`) issues ES256-signed tokens; tests use a local test key and a faked JWKS.
- Phase 9 is a local production setup: `docker-compose.prod.yml` (app, Nginx reverse proxy, Postgres), non-root containers,
  `/ready` plus a smoke test through Nginx, and `docs/runbook.md` for start, stop, backup and restore.
- Not built: SSH deploy job, server hardening (ufw, SSH keys), Let's Encrypt, image registry, rollback by image tag.

Consequences: No public URL for the portfolio, and server-level security is not demonstrated. The app code, migrations,
CI and the compose setup stay deployment-ready.

What would be needed to deploy later:
1. A host (any Ubuntu VPS) and a domain for HTTPS (Let's Encrypt).
2. A hosted Supabase project (or keep self-hosted auth) with ES256 signing keys; set `SUPABASE_URL` and the audience env vars.
3. A hosted or containerised Postgres 17, a backup schedule, and `alembic upgrade head` run before each release.
4. A container registry, images tagged by commit SHA, and a GitHub Actions deploy job over SSH that runs only on green main,
   takes a backup first, runs a smoke test, and rolls back to the previous tag on failure.
5. Server hardening: non-root user, SSH keys only, root login off, firewall with only 22, 80, 443 open.
6. Secrets in the host's secret store or environment, never in the repo.
