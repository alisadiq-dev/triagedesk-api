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

## Live check

Signs up a throwaway user, verifies the real token with our verifier, and checks that an unreachable JWKS fails closed.
It prints only non-secret facts (never the key or the token):

```bash
SUPABASE_PUBLISHABLE_KEY="$(supabase status -o env | grep '^PUBLISHABLE_KEY=' | cut -d= -f2- | tr -d '"')" \
  .venv/bin/python -m scripts.live_auth_check
```

Result on 2026-10-02 (CLI 2.119.0): JWKS had one EC P-256 ES256 key; the token header was `alg=ES256`, `typ=JWT` with a
`kid`; `aud="authenticated"`, `iss="http://127.0.0.1:54321/auth/v1"`, `is_anonymous=False` and `role="authenticated"`
claims were present; `JwtTokenVerifier` accepted it; a verifier pointed at a closed port failed closed with 503.

Not checked live (covered by unit tests with a local test key): expired, wrong-audience, wrong-issuer, tampered and
anonymous tokens, and JWKS key rotation.
