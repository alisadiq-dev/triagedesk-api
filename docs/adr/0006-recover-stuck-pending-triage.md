# ADR 0006: Recover tickets stuck in ai_status = pending

Status: PROPOSED (not built; waiting for the owner's approval)

## Problem
Triage runs in FastAPI BackgroundTasks after the response is sent (ADR 0001). If the process restarts or crashes between ticket
creation and the end of triage, that work is lost and the ticket stays `ai_status = pending` forever. Nothing retries it.

## What already protects us
`TriageRunner.run` is safe to call twice: it reads the ticket first, skips anything that is not `pending`, and writes under a row
lock that re-checks `pending`. A run always ends in `completed` or `failed` (keyword fallback), so it never loops.

## Options
| Option | Idea | Cost | Verdict |
|---|---|---|---|
| A. Recovery sweeper (recommended) | A small asyncio task started in the app lifespan re-runs triage for old pending tickets, at startup and then every minute | One small module, 3 settings, no schema change, no new dependency, no new endpoint | Recommended |
| B. Startup-only sweep | Same, but only once at startup | Smaller, but a ticket whose triage was lost to a crash waits until the next restart | Simpler fallback |
| C. Manual admin endpoint | `POST /admin/triage/retry` | Changes the approved API contract; relies on a human noticing | Rejected |
| D. Durable job table and worker | Persist jobs, poll them | Re-implements a queue; against the "keep it simple, no Celery" decision | Rejected |

## Proposed design (option A)
- On startup, then every `AI_RECOVERY_INTERVAL_SECONDS` (default 60), select up to `AI_RECOVERY_BATCH` (default 10) tickets with
  `ai_status = 'pending'` and `created_at` older than `AI_RECOVERY_GRACE_SECONDS` (default 120, longer than the 15 s model
  timeout, so a triage that is simply running is never taken over), oldest first.
- For each, call the existing `TriageRunner.run`. It resolves the ticket to `completed` or `failed`, so each ticket is retried at
  most once per sweep and leaves the pending state after the first successful write.
- Two sweepers (two app instances, or an overlapping sweep) would waste a paid model call, so each ticket is guarded by a
  Postgres advisory lock (`pg_try_advisory_lock`, held on one connection while the run happens, released when the connection
  ends, so a crash cannot leave it locked). The row-lock check inside the runner remains the correctness guarantee.
- Setting `AI_RECOVERY_INTERVAL_SECONDS=0` disables the sweeper (used in tests that need quiet).
- One log line per sweep: `triage_recovery` with the number found and the outcomes, no ticket text.

## Tests (written first)
Old pending ticket is triaged; recent pending ticket is left alone; completed and failed tickets are left alone; batch limit is
respected; two concurrent sweepers call the model once; sweep survives a model that raises; a "crash" (pending ticket created
without any task) is recovered; disabled when the interval is 0.

## Consequences
No ticket stays pending after a restart, with at most one interval plus the grace period of delay. Tickets are only recovered
while the app is running (if the app stays down, nothing runs, which is expected).
Not solved: triage is still at-most-once per attempt (a crash mid model call wastes that one call).
