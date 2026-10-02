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
- `make run` and the Docker image were checked locally only; there is no deployed environment (local-only project, ADR 0005).

### Notes
- Port 5433 on the dev machine is used by a local Postgres, so the test DB uses 55432.
- The coverage gate (85% on `app/services`, `app/ai`) starts when those packages exist.
- Controls verified in Phase 0: a fake `.env` commit is rejected by pre-commit.

## Phase 1: Data model, Alembic, first migration, seed (done)

Delivered: models for profiles, categories, sla_policies, tickets, ticket_comments, ticket_events; Alembic async setup;
migration `0001` with a working downgrade; indexes (including partial SLA indexes and a GIN full-text index);
SQL and Python breach rule that agree (tested on a case table); idempotent seed (`make seed`); `make migrate` is real;
CI applies migrations (upgrade, downgrade, upgrade) on a fresh Postgres.

Checks: 64 tests, ruff and mypy clean, pip-audit clean, CI green on GitHub.

### Five-axis self-review
| Axis | Finding | Severity | Status |
|---|---|---|---|
| Correctness | Enum column width was derived from the longest value (a bad value failed on length, and a longer value later would need an ALTER) | Medium | Fixed: fixed `VARCHAR(32)` plus CHECK |
| Correctness | Autogenerate emitted serial keys instead of identity | Low | Fixed by hand in model and migration |
| Readability | Migration file is long but flat and reviewed by hand | Info | Accepted |
| Architecture | No ORM relationships on purpose, so no lazy loads; repositories will join explicitly | Info | Intentional |
| Security | Seed prints no secrets; bootstrap admin comes from an env var; DB rejects customer internal notes | Info | Tested |
| Performance | Indexes follow the approved model; real numbers (EXPLAIN ANALYZE) come in Phase 7 | Info | Deferred to Phase 7 |

Simplification pass: no behavior changes needed; removed the obsolete zero-revision migration test.

### Not verified yet
- Phase 7 baseline for EXPLAIN ANALYZE needs a copy of the schema without the indexes; it will be built on a throwaway database.

### Carried forward
- Role-change endpoint (Phase 3) must write one structured log line (actor, target, old role, new role, request id), with a test.

## Phase 2: Auth and RBAC (done)

Delivered: Supabase ES256 token verification against the issuer's JWKS (`app/core/jwt_auth.py`), profile creation on the first
valid request (idempotent, concurrent-safe), `Actor` with the role read from `profiles`, `get_actor` and `require_roles`
dependencies, 401/403/503 error types, local Supabase stack config and bootstrap (`docs/local-supabase.md`), live check script.

Checks: 157 tests, ruff and mypy clean, pip-audit clean, CI green on GitHub.
Live check against `supabase start` (CLI 2.119.0): JWKS has one ES256 P-256 key, real token `aud="authenticated"`,
`iss="http://127.0.0.1:54321/auth/v1"`, `is_anonymous=False` present; the real token verifies; an unreachable JWKS fails closed (503).

### Doubt pass (JWT and RBAC)
| Claim | Doubt | Result |
|---|---|---|
| Only ES256 tokens from the issuer are accepted | Algorithm confusion (HS256 signed with the public key), `alg=none`, other ES curves | Tested; header is checked before any key lookup |
| Every invalid token looks the same | Different messages or headers leak the reason | Tested over HTTP: byte-identical 401 for 9 kinds of bad token. Only the outage 503 differs (fail-closed design) |
| Role never comes from the token | A `role: admin` claim could be trusted somewhere | Tested end to end: customer stays customer, admin route 403 |
| Missing token costs nothing | JWKS or DB work before the 401 | Tested: no verifier call, no DB created |
| JWKS failure fails closed | Stale keys served, request floods, log floods | Expired cache never served, backoff, one log line per real failure, Retry-After |
| Profile creation cannot be abused | Race, duplicate, role escalation | 5 concurrent first requests give one row; existing role never changed; insert sets no role |

### Security audit (security-auditor persona)
No Critical or High. Fixed: https-only `SUPABASE_URL` (loopback exempt), no JWKS redirects and a size cap, clock-skew leeway,
single log line per outage, no write per request, hidden SQL parameters, and test gaps (end-to-end over HTTP, nbf/iat, aud lists, ES384).
Accepted: a removed key can stay valid for up to 300 s (cache lifetime); `aud` may be a list that contains ours (standard);
a slow-drip JWKS response could hold the fetch lock beyond the per-phase timeout (needs a compromised issuer).
`httpx2` is Pydantic's httpx fork (github.com/pydantic/httpx2), confirmed legitimate; Starlette's docs recommend it.

### Five-axis self-review
| Axis | Finding | Severity | Status |
|---|---|---|---|
| Correctness | Leeway 0 would reject fresh tokens under small clock skew | Low | Fixed |
| Correctness | Duplicate kids in a JWKS silently overwrite | Info | Accepted |
| Readability | `_should_fetch` had a redundant condition | Low | Simplified |
| Architecture | Verifier depends on a `KeyProvider` protocol, so tests need no network | Info | Intentional |
| Security | See audit above | - | Fixed or accepted |
| Performance | Existing users cost one SELECT per request; Phase 7 may cache profiles if measurements justify it | Info | Deferred to Phase 7 |

### Not verified live
Expired, wrong-audience, wrong-issuer, tampered and anonymous tokens and key rotation against the real stack
(anonymous sign-ins are disabled in the local config). They are covered by unit and HTTP tests with a local test key.
