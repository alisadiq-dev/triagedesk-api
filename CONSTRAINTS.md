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
| 10 | Latency | p95 target for the ticket list endpoint: TBD in Phase 7 after the first measurement (10,000 seeded tickets) | Phase 7 benchmark | CI smoke later |

Notes:
- The coverage gate (3) is activated when `app/services` and `app/ai` exist; until then it is not enforced, and I will say so in `docs/progress.md`.
- Constraint 4 starts when the first endpoint with roles exists (Phase 2 and 3). `/health` and `/ready` are public.
- Weakening a constraint, or the check that enforces it, counts as lowering a threshold and needs your approval.
