# Progress

## Phase 0: Repo setup and tooling (done)

Delivered: pinned tooling (uv-compiled requirements), settings from env vars, `/health` and `/ready`, shared error format,
async DB session with explicit pool, Dockerfile (non-root) and docker-compose (app + Postgres 17 test DB on port 55432),
Makefile (`run`, `test`, `lint`, `migrate`/`seed` stubs, `audit`), pre-commit (ruff, mypy, no suppressions, no `.env`),
CLAUDE.md, CONSTRAINTS.md, `.env.example` (kept in sync by a test), ADRs for the four locked decisions, GitHub Actions CI.

Checks: 22 tests, 98% coverage, ruff and mypy clean, pip-audit clean, CI green on GitHub.

### Five-axis self-review
| Axis | Finding | Severity | Status |
|---|---|---|---|
| Correctness | `/ready` returned a generic 500 when `DATABASE_URL` was missing | Medium | Fixed with a test |
| Correctness | One setting (`DB_POOL_TIMEOUT_SECONDS`) also sets the asyncpg connect timeout | Low | Accepted, revisit if they need to differ |
| Readability | none | - | - |
| Architecture | Database is created lazily on first use so the app starts without a DB URL | Info | Intentional |
| Security | Error handlers never echo input or exception text; DB URL is a SecretStr | Info | Tested |
| Performance | Pool configured explicitly, pre-ping on | Info | Tuned in Phase 7 |

Simplification pass: removed the unused test helper and the `assert`-based handlers (replaced by typed decorators).

### Not verified yet
- Behaviour of asyncpg and SQLAlchemy through the Supabase session pooler (needs the Supabase project; live check later).
- `make run` and the Docker image were checked locally only; no deployed environment exists yet.

### Notes
- Port 5433 on the dev machine is used by a local Postgres, so the test DB uses 55432.
- The coverage gate (85% on `app/services`, `app/ai`) starts when those packages exist.
- Controls verified in Phase 0: a fake `.env` commit is rejected by pre-commit.
