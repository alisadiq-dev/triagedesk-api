# ADR 0010: Signups off on the token issuer; demo users through the admin API

Status: accepted (2026-10-02)

Context: Every valid Supabase token gets a customer profile on its first request. If anyone can sign up, anyone can mint accounts, which
multiplies every per-user limit and the model bill (security review item M3).
Decision: `[auth] enable_signup = false` in `supabase/config.toml` (verified live: `POST /auth/v1/signup` is refused). Users are created with
the admin API and the stack's secret key by `scripts/create_demo_users.py`, which is idempotent, writes the generated password to the
git-ignored `supabase/demo_users.json` (mode 600) and never prints it, and uses a fixed admin id so `BOOTSTRAP_ADMIN_SUB` is known in
advance. Roles never come from tokens: the first admin is the seeded bootstrap profile; the agent is promoted through our own admin endpoint.
Consequences: Do not set `[auth.email] enable_signup = false` in this CLI: it disables email login altogether (`email_provider_disabled`).
The demo users are local fixtures, not a production user store. A hosted deployment would need its own decision on signups (ADR 0005 lists
what a deployment needs).
