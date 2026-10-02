"""Create local demo users (customer, agent, admin) with the local Supabase stack's admin API.

Signups are disabled in supabase/config.toml, so users can only be created with the admin (secret)
key. Safe to re-run. Needs the local stack and, for the role steps, the API:

  eval "$(supabase status -o env | sed 's/^/export SUPABASE_/')"  # SUPABASE_SECRET_KEY etc.
  .venv/bin/python -m scripts.create_demo_users

Environment: SUPABASE_URL (default http://127.0.0.1:54321), API_BASE_URL (default through Nginx,
http://127.0.0.1:8080), DEMO_USER_PASSWORD (optional; otherwise a random one is generated once).
The password and the user ids go to the git-ignored supabase/demo_users.json (mode 600). The script
never prints the password, tokens or keys.
"""

import asyncio
import json
import os
import secrets
import sys
from pathlib import Path

import httpx2

STATE_FILE = Path("supabase/demo_users.json")
USER_AGENT = "Mozilla/5.0 (compatible; triagedesk-demo-users/1.0)"
ROLES = {
    "customer": "demo-customer@example.com",
    "agent": "demo-agent@example.com",
    "admin": "demo-admin@example.com",
}
# Fixed so that BOOTSTRAP_ADMIN_SUB can be written down before the user exists. Local demo only.
ADMIN_ID = "5d1e0000-0000-4000-8000-000000000001"
EXIT_ADMIN_NOT_READY = 3


def _admin_headers(secret_key: str) -> dict[str, str]:
    return {"apikey": secret_key, "Authorization": f"Bearer {secret_key}"}


async def _find_user_id(auth: httpx2.AsyncClient, secret_key: str, email: str) -> str:
    page = 1
    while True:
        response = await auth.get(
            "/auth/v1/admin/users",
            params={"page": page, "per_page": 100},
            headers=_admin_headers(secret_key),
        )
        response.raise_for_status()
        users = response.json().get("users", [])
        for user in users:
            if user.get("email") == email:
                return str(user["id"])
        if len(users) < 100:
            raise RuntimeError(f"user {email} exists but was not found in the user list")
        page += 1


async def _ensure_user(
    auth: httpx2.AsyncClient, secret_key: str, role: str, email: str, password: str
) -> str:
    body: dict[str, object] = {"email": email, "password": password, "email_confirm": True}
    if role == "admin":
        body["id"] = ADMIN_ID
    response = await auth.post(
        "/auth/v1/admin/users", json=body, headers=_admin_headers(secret_key)
    )
    if response.status_code == 422 and response.json().get("error_code") == "email_exists":
        user_id = await _find_user_id(auth, secret_key, email)
        reset = await auth.put(
            f"/auth/v1/admin/users/{user_id}",
            json={"password": password, "email_confirm": True},
            headers=_admin_headers(secret_key),
        )
        reset.raise_for_status()
        return user_id
    response.raise_for_status()
    return str(response.json()["id"])


async def _sign_in(
    auth: httpx2.AsyncClient, publishable_key: str, email: str, password: str
) -> str:
    response = await auth.post(
        "/auth/v1/token",
        params={"grant_type": "password"},
        json={"email": email, "password": password},
        headers={"apikey": publishable_key},
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def _load_password(state: Path, given: str | None) -> str:
    if given:
        return given
    if state.exists():
        saved = json.loads(state.read_text()).get("password")
        if isinstance(saved, str) and saved:
            return saved
    return secrets.token_urlsafe(18)


def _write_state(state: Path, password: str, users: dict[str, dict[str, str]]) -> None:
    state.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps({"password": password, "users": users}, indent=2)
    descriptor = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(content + "\n")
    state.chmod(0o600)


async def run(
    auth: httpx2.AsyncClient,
    api: httpx2.AsyncClient,
    state: Path,
    secret_key: str,
    publishable_key: str,
    password: str | None = None,
) -> int:
    password = _load_password(state, password)
    users: dict[str, dict[str, str]] = {}
    for role, email in ROLES.items():
        users[role] = {
            "email": email,
            "id": await _ensure_user(auth, secret_key, role, email, password),
        }
    _write_state(state, password, users)
    print(f"demo users written to {state} (mode 600; the password is in that file)")

    tokens = {
        role: await _sign_in(auth, publishable_key, user["email"], password)
        for role, user in users.items()
    }
    roles: dict[str, str] = {}
    for role, token in tokens.items():  # the first authenticated request creates the profile
        me = await api.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        me.raise_for_status()
        roles[role] = me.json()["role"]
    print("roles in the API:", ", ".join(f"{r}={roles[r]}" for r in ROLES))

    if roles["admin"] != "admin":
        print(
            "The admin user is not an admin in the API database yet. Set\n"
            f"  BOOTSTRAP_ADMIN_SUB={users['admin']['id']}\n"
            "run the seed (make seed, or `make prod-seed` for the local production stack), "
            "then run this script again to promote the agent."
        )
        return EXIT_ADMIN_NOT_READY
    if roles["agent"] != "agent":
        promoted = await api.patch(
            f"/api/v1/users/{users['agent']['id']}",
            json={"role": "agent"},
            headers={"Authorization": f"Bearer {tokens['admin']}"},
        )
        promoted.raise_for_status()
        print("promoted demo-agent@example.com to agent")
    return 0


async def main() -> int:
    secret_key = os.environ.get("SUPABASE_SECRET_KEY", "")
    publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    print(f"secret key length: {len(secret_key)}, publishable key length: {len(publishable_key)}")
    if not secret_key or not publishable_key:
        print("SUPABASE_SECRET_KEY and SUPABASE_PUBLISHABLE_KEY must be set (see the docstring)")
        return 2
    supabase_url = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321")
    api_url = os.environ.get("API_BASE_URL", "http://127.0.0.1:8080")
    headers = {"User-Agent": USER_AGENT}
    async with (
        httpx2.AsyncClient(base_url=supabase_url, headers=headers, timeout=15) as auth,
        httpx2.AsyncClient(base_url=api_url, headers=headers, timeout=15) as api,
    ):
        return await run(
            auth, api, STATE_FILE, secret_key, publishable_key, os.environ.get("DEMO_USER_PASSWORD")
        )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
