# Local Supabase stack (token issuer)

The API never talks to a hosted Supabase. For local runs and the live auth check, the Supabase CLI stack
(`supabase start`) acts as the identity provider and signs access tokens with an **ES256** key. The app only
verifies tokens: signature (key from the issuer's JWKS), `exp`, `iss`, `aud`, pinned algorithm ES256.
The application database is the separate Docker Postgres 17 (see `docker-compose.yml`), not Supabase's Postgres.

## One-time bootstrap

`supabase/signing_keys.json` holds a **private** key. It is git-ignored (`.gitignore`); never commit it.

```bash
# 1. Create the keys file as an empty array, then append an ES256 key to it
echo '[]' > supabase/signing_keys.json
supabase gen signing-key --algorithm ES256 --append

# 2. supabase/config.toml already points at it:
#    [auth]
#    signing_keys_path = "./signing_keys.json"
```

Without the file, `supabase start` fails with `StartInvalidConfigError`. The CLI expects a JSON **array** of keys,
which is why the file is created as `[]` first.

## Start and stop

```bash
# Only what auth needs (database, auth service, API gateway):
supabase start -x realtime,storage-api,imgproxy,mailpit,postgrest,postgres-meta,studio,edge-runtime,logflare,vector,supavisor
supabase status          # URLs and keys (local development defaults)
supabase stop            # stops the containers
```

The issuer is `http://127.0.0.1:54321/auth/v1` and the JWKS is `<issuer>/.well-known/jwks.json`.
Settings: `SUPABASE_URL` (default `http://127.0.0.1:54321`) and `SUPABASE_JWT_AUDIENCE` (default `authenticated`).

## Signups are off; demo users

`supabase/config.toml` sets `[auth] enable_signup = false`, so nobody can create an account with the public key
(`POST /auth/v1/signup` is refused with 422). Users are created with the **admin API** and the stack's secret key.
Do not set `[auth.email] enable_signup = false`: in this CLI that disables the email provider and password
login stops working (`email_provider_disabled`).

Local demo users (one customer, one agent, one admin), safe to re-run:

```bash
eval "$(supabase status -o env | sed 's/^/export SUPABASE_/')"   # SUPABASE_SECRET_KEY, SUPABASE_PUBLISHABLE_KEY, ...
.venv/bin/python -m scripts.create_demo_users                      # needs the API (default http://127.0.0.1:8080)
```

- Users: `demo-customer@example.com`, `demo-agent@example.com`, `demo-admin@example.com`. The password is generated once
  and written, with the user ids, to the git-ignored `supabase/demo_users.json` (mode 600). Set `DEMO_USER_PASSWORD` to choose it.
  The script never prints the password, tokens or keys.
- The admin user has the fixed id `5d1e0000-0000-4000-8000-000000000001`, so `BOOTSTRAP_ADMIN_SUB` can be set before the user
  exists. Seed once (`make seed`, or `make prod-seed`) and re-run the script: it then promotes the agent through the API
  (`PATCH /api/v1/users/{id}`, as the admin). Until then it exits with code 3 and says what to do.
- Sign in as one of them for a token: `POST /auth/v1/token?grant_type=password` with the publishable key as `apikey`
  (see `scripts/demo.py`).

## Live check

Checks that open signup is refused, creates a throwaway user with the admin API (and deletes it afterwards), verifies the
real token with our verifier, and checks that an unreachable JWKS fails closed. It prints only non-secret facts
(never a key or the token):

```bash
eval "$(supabase status -o env | sed 's/^/export SUPABASE_/')"
.venv/bin/python -m scripts.live_auth_check
```

Do not paste the output of `supabase start` or `supabase status` anywhere: they print the stack's keys.

Result on 2026-10-02 (CLI 2.119.0): JWKS had one EC P-256 ES256 key; the token header was `alg=ES256`, `typ=JWT` with a
`kid`; `aud="authenticated"`, `iss="http://127.0.0.1:54321/auth/v1"`, `is_anonymous=False` and `role="authenticated"`
claims were present; `JwtTokenVerifier` accepted it; a verifier pointed at a closed port failed closed with 503.

Not checked live (covered by unit tests with a local test key): expired, wrong-audience, wrong-issuer, tampered and
anonymous tokens, and JWKS key rotation.

Result on 2026-10-02 after disabling signups (CLI 2.119.0): open signup refused (422); admin-created user signed in with a
password; the token was ES256 with the same `aud`, `iss` and claims as above and verified; an unreachable JWKS failed closed.
