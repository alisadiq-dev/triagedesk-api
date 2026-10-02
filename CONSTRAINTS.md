# CONSTRAINTS.md

Status: APPROVED (2026-10-02, owner moved to the no-stop workflow without changes). Thresholds can be raised but never lowered without the owner's explicit approval.

| # | Constraint | Threshold | Cheapest place | Backstop |
|---|---|---|---|---|
| 1 | Lint | Ruff clean, zero violations | pre-commit | CI |
| 2 | Types | mypy strict clean | pre-commit | CI |
| 3 | Coverage | at least 85% on `app/services` and `app/ai` | CI (pytest-cov gate) | review |
| 4 | Role permissions | every role has a permission test on every endpoint (matrix test fails if an endpoint lacks one) | CI | phase review |
| 5 | No suppressions | no new `noqa`, `type: ignore`, skip or xfail without approval | pre-commit grep | CI grep |
| 6 | No secrets | no `.env`, keys or tokens in git | pre-commit | CI |
| 7 | Dependencies | pip-audit reports no known vulnerabilities | CI | `make audit` |
| 8 | N+1 | list endpoints run a constant number of queries regardless of row count (query-count test) | CI | review |
| 9 | Migrations | `alembic upgrade head` works on a fresh Postgres, and every migration has a working downgrade | CI (from Phase 1) | review |
| 10 | Latency | p95 of 50 ms or less for `GET /api/v1/tickets`, in every scenario of `scripts/benchmark_list.py`. Conditions: local machine, single client (one request at a time), 10,000 seeded tickets, in process (no network, no Nginx). Approved by the owner 2026-10-02 from the first measurement (worst p95 about 23 ms, `docs/performance.md`) | `make bench` (manual) | CI smoke later |

Notes:
- The coverage gate (3) is active since Phase 5 (`make test` fails under 85% on `app/services` and `app/ai`; measured with `concurrency = greenlet` so async service code is counted). Current: 96%.
- Constraint 4 starts when the first endpoint with roles exists (Phase 2 and 3). `/health` and `/ready` are public.
- Weakening a constraint, or the check that enforces it, counts as lowering a threshold and needs your approval.
