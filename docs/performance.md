# Performance baseline (Phase 7)

Measured on 2026-10-02 with `scripts/benchmark_list.py` (`make bench`) on the owner's Mac: local Docker Postgres 17 (tmpfs), the app
called in process through ASGI (so these numbers are app plus database, without network or Nginx), 200 sequential requests per
scenario after 10 warm-ups, one connection at a time. The script creates a throwaway database, migrates it, seeds 10,000 tickets
(500 customers, 20 agents, mixed statuses, priorities and ages over 90 days, searchable words in titles and bodies), runs
`ANALYZE`, and drops the database afterwards.

## Latency (in process, 10000 tickets, 200 requests per scenario)

| Scenario | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|
| admin, default (newest first) | 7.1 | 9.8 | 11.8 | 41.8 |
| admin, status=open | 7.0 | 10.3 | 11.4 | 11.5 |
| admin, priority=urgent&category_id=2 | 8.5 | 11.6 | 14.6 | 19.9 |
| admin, sla_breached=true | 8.5 | 11.5 | 13.4 | 13.7 |
| admin, q=invoice (full text) | 8.9 | 11.7 | 13.2 | 16.5 |
| admin, sort by resolution due | 13.2 | 15.1 | 17.8 | 26.4 |
| admin, deep page 100 of 100 | 19.5 | 22.7 | 31.1 | 50.0 |
| agent, default (unassigned or own) | 6.8 | 9.2 | 11.6 | 12.1 |
| customer, default (own tickets) | 5.3 | 8.8 | 9.6 | 10.3 |

## Constraint 10 (p95 target)

Target: **p95 of 50 ms or less** for every list scenario above, on 10,000 tickets, measured this way. The worst measured p95 is the deep
page (page 100 at page size 100, about 23 ms); the default list is about 10 ms. The target leaves roughly twice the worst case and five
times the default as headroom for slower machines. This is a proposal: `CONSTRAINTS.md` keeps the target as TBD until the owner confirms the number.

## What the time is made of

The database work for a default page is about 0.2 ms (`Execution Time` below), yet the request takes about 7 ms. The rest is
not broken down by this benchmark; it is most likely application work that does not depend on the page (token check, the profile
lookup, the count query, building and validating up to 20 response objects). That split is an inference, not a measurement. Phase 2 noted that profile caching may be considered if measurements justify it. At this size they do not: the whole
request is well under the target.

## EXPLAIN ANALYZE: with and without the secondary indexes (same 10,000 rows)

Plan of the page query for one admin request per scenario (the count query is separate). "Without" drops every index on `tickets`
except the primary key, then runs `ANALYZE`.

| Scenario | Scan with indexes | ms | Scan without | ms |
|---|---|---|---|---|
| admin, default (newest first) | Index Scan Backward using ix_tickets_created_at | 0.190 | Seq Scan on tickets | 6.862 |
| admin, status=open | Index Scan Backward using ix_tickets_created_at | 0.164 | Seq Scan on tickets | 2.447 |
| admin, priority=urgent&category_id=2 | Index Scan Backward using ix_tickets_created_at | 0.512 | Seq Scan on tickets | 1.295 |
| admin, sla_breached=true | Index Scan Backward using ix_tickets_created_at | 0.168 | Seq Scan on tickets | 4.897 |
| admin, q=invoice (full text) | Index Scan Backward using ix_tickets_created_at | 0.359 | Seq Scan on tickets | 1.739 |
| admin, sort by resolution due | Seq Scan on tickets | 6.076 | Seq Scan on tickets | 5.536 |
| admin, deep page 100 of 100 | Index Scan Backward using ix_tickets_created_at | 10.325 | Seq Scan on tickets | 14.350 |

Reading it honestly:
- At 10,000 rows a sequential scan costs only 1 to 7 ms, so the indexes matter little here. What they buy is the plan shape at larger sizes: the newest-first pages stop after about 21 index entries, which stays flat as the table grows, while the sequential scan plus sort grows linearly.
- Postgres uses the `created_at` index for every newest-first list, including filtered ones, and filters rows as it walks them. Planner choice, not a bug; the partial SLA indexes and the status index are not picked for these queries.
- Sorting by `resolution_due_at` does a sequential scan and a sort even with indexes: the partial index only covers tickets that are not yet resolved, and the endpoint sorts all tickets. Cost at 10,000 rows: about 6 ms. If that sort matters at a larger scale, a plain index on `resolution_due_at` (and on `first_response_due_at`) would be a new migration; not added now, since no measurement asks for it.
- Deep pages cost the most (offset 9,900 reads 10,000 rows): about 10 ms in the database. Offset pagination was an approved decision (contract decision 2); a keyset option is not planned.
- Full-text search was fast without the GIN index at this size too; the GIN index is what keeps it flat at larger sizes (not measured here).

## Not measured
- Concurrent load (one request at a time here), network and Nginx overhead, a table larger than 10,000 rows, a cold cache, or the AI path (the model is mocked or off).
- Row counts that make the sequential-scan baseline slow, so the benefit of each index at scale is reasoned, not measured.
