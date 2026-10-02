# ADR 0009: Local production topology, proxy trust and logs

Status: accepted (2026-10-02; refines ADR 0005)

Decision: `docker-compose.prod.yml` runs Postgres 17.11 (named volume, no published port), a one-shot `alembic upgrade head`, the app, and
unprivileged Nginx published on `127.0.0.1` only.
- One uvicorn worker (ADR 0007), `--proxy-headers`, and `--forwarded-allow-ips` set to the Nginx container's fixed address on a fixed
  subnet, so `X-Forwarded-For` is honoured from that one address. Nginx overwrites the header with the address it sees, never appends.
- Nginx forwards only `/health`, `/ready` and `/api/v1/`; `client_max_body_size 64k`; errors it produces use the API error format; one
  request id (`X-Request-ID`) is shared with the app's logs. The interactive docs and OpenAPI are off by default and not routed.
- Logs: uvicorn's access log is off. The app writes one JSON line per request with the route template, never the query string, path values,
  headers or bodies (tested). The Nginx log uses `$uri`, not `$request`. HTTP client loggers are set to WARNING.
- Containers: non-root, read-only root, capabilities dropped, no new privileges.
- The Supabase stack runs on the host, so the container fetches signing keys from `SUPABASE_JWKS_BASE_URL` (`host.docker.internal`) while the
  `iss` claim is still checked against `SUPABASE_URL`. Plain http to a non-loopback host needs the explicit
  `SUPABASE_JWKS_ALLOW_PLAIN_HTTP=true` and is for the local stack only.
- Secrets live in an env file outside the repo (`deploy/init_secrets.sh` creates it once). Backups are `pg_dump` custom format
  (`deploy/backup.sh`, mode 600, git-ignored); restore needs `RESTORE_CONFIRM=yes` (`deploy/restore.sh`).
Consequences: On Docker Desktop all local clients arrive from the network gateway, so they share one rate-limit bucket. No TLS (HSTS is not
set). A second project needs its own port, subnet and Nginx address. The runbook is `docs/runbook.md`.
