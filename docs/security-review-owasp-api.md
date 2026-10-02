# OWASP API Security Top 10 (2023) review, Phase 8

Reviewed 2026-10-02 by me (self-review) and by an independent read-only pass of the security-auditor persona over the whole API.
Earlier audits: Phase 2 (auth) and Phase 3 (RBAC) in `docs/progress.md`. No Critical or High findings in this pass.

| # | Risk | Controls | Backing tests | Verdict |
|---|---|---|---|---|
| API1 | Broken object level authorization | Visibility is part of the query (`app/services/visibility.py`); a ticket you may not see is a 404 with the same body; every sub-resource goes through the same lookup first | permission matrix (every role on every route), `test_tickets_api`, `test_events_api`, `test_sla_api` | OK |
| API2 | Broken authentication | ES256 only, `alg`/`kid` checked before any key fetch, `aud`/`iss`/`exp` required, anonymous tokens rejected, identical 401 body, role from the database, fail-closed 503 | `test_jwt_verifier`, `test_jwks_provider`, `test_auth_dependencies` | OK |
| API3 | Broken object property level authorization | Request bodies forbid extra fields; customer responses use allowlist schemas; the AI draft is hidden from non-assignee agents; list filters on hidden fields are 403 for customers | `test_ticket_schemas`, allowlist tests, `test_ticket_filters_api` | OK |
| API4 | Unrestricted resource consumption | Page size capped at 100 and page at 1,000,000; body size limit (413, also chunked); per-IP and per-user rate limits; global cap on model calls; model timeout; recovery batch and attempt caps; DB pool limits | `test_rate_limits_api`, `test_hardening_middleware`, `test_model_budget`, `test_triage_recovery`, `test_query_counts` | Fixed (audit M1, M2, M3, L2, L5), see below |
| API5 | Broken function level authorization | Role checks in services; route-coverage meta-test fails if a route is missing from the matrix | `test_permission_matrix` | OK |
| API6 | Unrestricted access to sensitive business flows | Ticket creation (costs a model call) and comments are limited per user; the model can only set AI fields | `test_rate_limits_api`, `test_triage_runner` injection tests | Accepted note L4 |
| API7 | Server side request forgery | No user-supplied URLs. Outbound calls: the JWKS URL (https unless loopback, no redirects) and the fixed Gemini endpoint | `test_jwks_provider` | OK |
| API8 | Security misconfiguration | Security headers and strict CSP, no `Server` header, generic 500, docs off by default, non-root container, secrets only from env, ports bound to 127.0.0.1 | `test_hardening_middleware`, `test_config`, Docker check below | Fixed (M4); Phase 9 items below |
| API9 | Improper inventory management | One version (`/api/v1`), every route is in the matrix, OpenAPI is off unless enabled | route-coverage meta-test, `test_hardening_middleware` | OK |
| API10 | Unsafe consumption of APIs | Gemini output is strictly validated (no extra keys, enums, active category id, length); errors keep only the status code; the key never reaches logs; httpx and SDK loggers are quiet | `test_triage_output`, `test_gemini_adapter`, `test_logging` | OK |

## Findings of the independent pass and what was done

| Id | Severity | Finding | Result |
|---|---|---|---|
| M1 | Medium | The per-IP API limiter was a router dependency, so unknown paths, wrong methods and bad bodies were never limited (contradicted the contract text) | Fixed: a middleware limits everything under `/api/v1` before routing and body reading; tests for 404, 405, bad body, request id and headers on a 429 |
| M2 | Medium | IPv6 clients could use a whole /64 as separate keys; eviction rebuilt a 10k table on the event loop | Fixed: IPv6 keyed by /64 (IPv4-mapped uses the IPv4 address); eviction is O(1) because the table is ordered by window start |
| M3 | Medium | No global cap on paid model calls (open signup could multiply accounts) | Fixed in code: global `AI_CALLS_PER_MINUTE` (default 60), then the keyword fallback. Not done: `max_output_tokens` (a limit that is too small could truncate the JSON; it needs another live call to tune, which I did not make). Phase 9: restrict or disable public signup on the Supabase stack (not verified here) |
| M4 | Medium | Docs and OpenAPI defaulted to on | Fixed: default off, `make run` turns them on |
| L1 | Low | A non-ASCII digit in `Content-Length` (for example a superscript two) made `int()` fail | Fixed with an ASCII check; unit test through the middleware (the HTTP server rejects such headers earlier anyway) |
| L2 | Low | An oversized chunked body logged a traceback per request | Fixed: the exception is handled in the middleware; test asserts no ERROR log |
| L3 | Low | Rate limits are per instance; two workers double them; the switch is one env var | Accepted; documented (run one worker). Phase 9: log a warning at boot if limits are disabled |
| L4 | Low | A hostile ticket can push the AI priority to urgent (shorter SLA, higher in the queue) | Accepted by design (PRD: AI sets priority; humans override; `priority_source` is visible to staff). Optional later: cap the AI at high without a keyword match |
| L5 | Low | The sweeper could pay for a ticket every minute if its result cannot be stored | Fixed: after `AI_RECOVERY_MAX_ATTEMPTS` (3) failed attempts a ticket is skipped (in memory; a restart resets it) |
| I5 | Info | httpx and SDK INFO logs print URLs | Fixed: those loggers are set to WARNING |

## Phase 9 notes (Nginx and the local production setup)

- Client IP: uvicorn sees Nginx's address. Set `--proxy-headers` with `--forwarded-allow-ips` to Nginx's address only, and make Nginx overwrite `X-Forwarded-For` with `$remote_addr`; otherwise one bucket covers everyone or the header can be spoofed.
- Nginx: `client_max_body_size 64k` (the app limit is the second line of defence), HSTS only if HTTPS is added (not planned, ADR 0005), and consider validating the `Host` header.
- Compose: `API_DOCS_ENABLED=false` (already the default), keep `RATE_LIMIT_ENABLED=true`, one uvicorn worker.
- Logs: the uvicorn access log prints query strings (customer search terms); turn it off or mask it behind Nginx.
- Supabase local stack: decide whether signup stays open (it multiplies accounts, so ticket flooding); not verified here.
- Dependencies are installed without hashes (`pip install -r requirements.txt`); pip-audit runs in CI. Hash pinning is an option. CI actions are pinned by tag, not SHA.

## Verified
`make audit` clean; the Docker image builds, runs as uid 10001, answers `/health` with the security headers and no `Server` header.
Not verified: behaviour under real concurrent load, a real Nginx in front, Supabase signup settings, and the live Gemini behaviour with the global cap.
