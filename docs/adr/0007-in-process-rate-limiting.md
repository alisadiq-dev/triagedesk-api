# ADR 0007: In-process rate limiting, one worker

Status: accepted (2026-10-02)

Context: The API needs rate limits on public routes and on the flows that cost money (each ticket costs a model call). Redis is ruled
out (ADR 0001).
Decision: A fixed-window limiter in the app process, bounded to 10,000 keys with O(1) eviction (`app/core/rate_limit.py`).
- Per client address (IPv6 grouped by /64), in a middleware before routing, authentication and body reading: `/health` and `/ready`
  (120 per minute, shared) and everything under `/api/v1`, including unknown paths (600 per minute).
- Per user, in route dependencies: ticket creation (10 per minute) and comment creation (30 per minute). Invalid requests count too.
- Globally: model calls (60 per minute); over the cap a ticket takes the keyword fallback.
- 429 uses the shared error format with `Retry-After`. Numbers and an off switch are env settings.
Consequences: No new service. The limits are per app instance and reset on restart, so the production compose file runs exactly one
uvicorn worker, and the README says so. A fixed window allows up to twice the limit across a window edge. Behind Nginx the client address
is only right if uvicorn trusts `X-Forwarded-For` from Nginx's fixed address alone (ADR 0009). If the API ever runs on more than one
instance, this ADR must be revisited (a shared store).
