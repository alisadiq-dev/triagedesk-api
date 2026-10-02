# Runbook: local production setup

The API, Nginx and Postgres run on this machine in Docker Compose (ADR 0005: no cloud, no TLS). Tokens come from the local
Supabase stack on the same machine (`docs/local-supabase.md`). Everything below is run from the repository root.

```
browser or curl ──> 127.0.0.1:8080 ──> nginx (unprivileged) ──> app (1 uvicorn worker) ──> db (Postgres 17.11, named volume)
                                                                  └── fetches signing keys from the Supabase stack on the host
```

Only Nginx is published, and only on `127.0.0.1`. The database has no published port. `/docs`, `/redoc` and `/openapi.json` are not
reachable (Nginx has no route for them, and the app has them off).

## First start

1. Docker running; the Supabase CLI installed.
2. Secrets file (once; never overwrites; prints no values):
   ```bash
   sh deploy/init_secrets.sh          # creates ~/.secrets/triagedesk-prod.env (mode 600) with a random POSTGRES_PASSWORD
   ```
   Optionally add `GEMINI_API_KEY=...` to it. Without a key every ticket takes the keyword fallback. The other names are in
   `deploy/prod.env.example`.
3. Token issuer (see `docs/local-supabase.md` for the one-time bootstrap):
   ```bash
   supabase start -x realtime,storage-api,imgproxy,mailpit,postgrest,postgres-meta,studio,edge-runtime,logflare,vector,supavisor >/dev/null
   ```
   Do not paste the output of `supabase start` or `supabase status` anywhere: they print the stack's keys.
4. Build and start (the one-shot `migrate` service runs `alembic upgrade head` first; `--wait` returns when everything is healthy):
   ```bash
   make prod-up
   make prod-seed                      # categories, SLA policies, and the bootstrap admin from BOOTSTRAP_ADMIN_SUB (safe to repeat)
   ```
5. Demo users, if you want them (customer, agent, admin; the admin has the fixed id that `BOOTSTRAP_ADMIN_SUB` already holds):
   ```bash
   eval "$(supabase status -o env | sed 's/^/export SUPABASE_/')"
   .venv/bin/python -m scripts.create_demo_users
   ```
6. Smoke test through Nginx:
   ```bash
   make smoke                                         # gateway checks (uses up this client's public rate budget for about a minute)
   PYTHONPATH=. .venv/bin/python -m scripts.smoke_test --signed-in   # needs the SUPABASE_* variables from step 5
   ```

## Day to day

| Task | Command |
|---|---|
| Start (or update after a code change) | `make prod-up` |
| Stop (keeps the data) | `make prod-down` |
| Status | `docker compose --env-file ~/.secrets/triagedesk-prod.env -f docker-compose.prod.yml ps` |
| Logs | `make prod-logs` (app: one JSON line per request with the route template; Nginx: method and path without the query) |
| Seed again | `make prod-seed` |

The Make targets use `PROD_ENV` (default `~/.secrets/triagedesk-prod.env`) and an optional `PROD_PROJECT` (Compose project name).
A different project needs its own `NGINX_PORT`, `PROD_SUBNET` and `NGINX_IP` in its env file, because the network address of Nginx is
fixed (it is the only address the app trusts for `X-Forwarded-For`).

## Backup

```bash
make prod-backup          # writes backups/triagedesk-<date>-<time>.dump (mode 600, git-ignored)
```

`pg_dump` custom format, taken while the stack runs (a consistent snapshot, no downtime). The script refuses to keep a file that
`pg_restore --list` cannot read. It holds the application database only: not the Supabase users, not the secrets file. Keep copies of
the dump files somewhere else if the data matters; they contain ticket text and email addresses, so treat them like the database.
A nightly cron line is enough: `0 3 * * * cd /path/to/triagedesk-api && make prod-backup`. Pruning old dumps is manual.

Before a release that changes the schema, take a backup first.

## Restore

Restoring REPLACES the current data. The script refuses to run without `RESTORE_CONFIRM=yes`.

```bash
make prod-backup                                   # take a safety copy of what is there now, if it is still readable
RESTORE_CONFIRM=yes make prod-restore FILE=backups/triagedesk-<date>-<time>.dump
```

It checks that the file is readable, starts `db`, stops `nginx` and `app` so nothing writes, runs
`pg_restore --clean --if-exists --no-owner --exit-on-error`, and starts everything again (the migrate service runs once more,
which does nothing when the restored schema is current).

Total loss (the volume is gone or damaged). **Destructive: `down -v` deletes the database volume.** Only do this when you have a
good dump:

```bash
make prod-down
docker compose --env-file ~/.secrets/triagedesk-prod.env -f docker-compose.prod.yml down -v   # removes the named volume
make prod-up                                       # fresh, empty, migrated database
RESTORE_CONFIRM=yes make prod-restore FILE=backups/<good dump>.dump
```

Then run the smoke test. The demo users live in Supabase, not in the dump; their roles are in the dump, so they keep working.

## What to know about running it

- **One worker, per-instance limits.** The rate limits are in the app process, with no shared store. Run exactly one uvicorn worker (the
  compose file does). A second instance or worker would double every limit, and a restart resets the counters.
  Per client: 120 per minute on `/health` and `/ready`, 600 per minute on `/api/v1`; per user: 10 tickets and 30 comments per minute;
  globally: 60 model calls per minute. All are env settings (`.env.example`).
- **Client address.** Nginx overwrites `X-Forwarded-For` with the address it sees, and uvicorn trusts that header only from Nginx's fixed
  address. On Docker Desktop every connection from this machine arrives from the Docker network gateway, so all local clients share one
  rate-limit bucket; that is expected for a one-machine setup.
- **Signups are off** on the local Supabase stack. Create users with `scripts/create_demo_users.py` (or the admin API).
- **Logs** hold no query strings, tokens, ticket text or secrets. Nginx and the app share one request id per request (`X-Request-ID`).
- **Request size:** 64 KiB, enforced by Nginx and by the app.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `make prod-up` says POSTGRES_PASSWORD is missing | the env file does not exist or is not the one in `PROD_ENV`; run `deploy/init_secrets.sh` |
| `/ready` is 503 | the database is down or unhealthy: `docker compose ... logs db` |
| Every authenticated request is 503 `auth_unavailable` | the Supabase stack is not running, or the app cannot reach it: check `supabase status`, and that `SUPABASE_JWKS_BASE_URL` is reachable from the container (`http://host.docker.internal:54321`) |
| Authenticated requests are 401 with a valid token | the token's issuer differs from `SUPABASE_URL` (default `http://127.0.0.1:54321`), or the audience differs |
| 429 on `/health` during a smoke test | the rate-limit check used up the public budget; wait a minute |
| Network address conflict on start | another network uses `172.29.77.0/24`; set `PROD_SUBNET` and `NGINX_IP` in the env file |
| A ticket stays `pending` | the sweeper retries after 2 minutes (up to 3 failed attempts); check the app logs for `triage_outcome` and `triage_recovery` |

## Verified, and not

Verified on 2026-10-02 against a throwaway Compose project (own volume, port and subnet; removed afterwards): build, migrations, seed,
demo users and role promotion, `scripts/smoke_test.py --signed-in` through Nginx (14 checks, including real Supabase tokens, background
triage and a spoofed `X-Forwarded-For`), log content (no query strings; the request id matches between Nginx and the app),
container hardening (non-root, read-only root, only Nginx published), `make prod-backup`, the refusal to restore without confirmation,
a restore over modified data, and a total-loss restore into a brand-new volume followed by a passing smoke test.

Not verified: behaviour under concurrent load, a restart of the Docker daemon or of the machine (all services are `unless-stopped`),
the Gemini model inside the container (the throwaway project ran without a key, so tickets took the keyword fallback), and the
real `triagedesk-prod` project, which was not started during the build (the owner starts it with `make prod-up`).
