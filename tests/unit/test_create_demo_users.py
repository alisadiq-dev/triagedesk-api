import json
import stat
import uuid
from pathlib import Path

import httpx2
import pytest

from scripts.create_demo_users import ROLES, run

ADMIN_KEY = "sb_secret_FAKE_KEY_FOR_TESTS"
PUBLISHABLE = "sb_publishable_FAKE_KEY_FOR_TESTS"
OWN_CHOICE = "my-own-demo-credential-1"


class FakeStack:
    """A tiny stand-in for the Supabase admin API and for our API."""

    def __init__(self, admin_role: str = "admin") -> None:
        self.users: dict[str, dict[str, str]] = {}  # email -> {"id", "password"}
        self.profile_roles: dict[str, str] = {}
        self.admin_role = admin_role
        self.requests: list[tuple[str, str]] = []

    def supabase(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append((request.method, request.url.path))
        path = request.url.path
        if path.startswith("/auth/v1/admin/"):
            if request.headers.get("apikey") != ADMIN_KEY:
                return httpx2.Response(401, json={})
            return self._admin(request)
        if path == "/auth/v1/token":
            body = json.loads(request.content)
            user = self.users.get(body["email"])
            if request.headers.get("apikey") != PUBLISHABLE or user is None:
                return httpx2.Response(400, json={})
            if user["password"] != body["password"]:
                return httpx2.Response(400, json={})
            return httpx2.Response(200, json={"access_token": f"token-for-{user['id']}"})
        return httpx2.Response(404)

    def _admin(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.method == "POST" and path == "/auth/v1/admin/users":
            body = json.loads(request.content)
            if body["email"] in self.users:
                return httpx2.Response(422, json={"error_code": "email_exists"})
            self.users[body["email"]] = {
                "id": body.get("id") or str(uuid.uuid4()),
                "password": body["password"],
            }
            return httpx2.Response(200, json={"id": self.users[body["email"]]["id"]})
        if request.method == "GET" and path == "/auth/v1/admin/users":
            return httpx2.Response(
                200, json={"users": [{"id": u["id"], "email": e} for e, u in self.users.items()]}
            )
        if request.method == "PUT":
            user_id = path.rsplit("/", 1)[1]
            body = json.loads(request.content)
            for user in self.users.values():
                if user["id"] == user_id:
                    user["password"] = body["password"]
            return httpx2.Response(200, json={})
        return httpx2.Response(404)

    def api(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append((request.method, request.url.path))
        user_id = request.headers.get("authorization", "").removeprefix("Bearer token-for-")
        if request.url.path == "/api/v1/me":
            role = self.profile_roles.setdefault(user_id, "customer")
            if user_id == self._admin_id():
                role = self.profile_roles[user_id] = self.admin_role
            return httpx2.Response(200, json={"id": user_id, "role": role})
        if request.method == "PATCH" and request.url.path.startswith("/api/v1/users/"):
            if self.profile_roles.get(user_id) != "admin":
                return httpx2.Response(403, json={})
            target = request.url.path.rsplit("/", 1)[1]
            self.profile_roles[target] = json.loads(request.content)["role"]
            return httpx2.Response(200, json={})
        return httpx2.Response(404)

    def _admin_id(self) -> str | None:
        user = self.users.get("demo-admin@example.com")
        return user["id"] if user else None


def clients(stack: FakeStack) -> tuple[httpx2.AsyncClient, httpx2.AsyncClient]:
    return (
        httpx2.AsyncClient(transport=httpx2.MockTransport(stack.supabase), base_url="http://auth"),
        httpx2.AsyncClient(transport=httpx2.MockTransport(stack.api), base_url="http://api"),
    )


async def do_run(stack: FakeStack, state: Path, password: str | None = None) -> int:
    auth, api = clients(stack)
    async with auth, api:
        return await run(auth, api, state, ADMIN_KEY, PUBLISHABLE, password)


async def test_creates_one_user_per_role_and_records_them_in_a_private_file(
    tmp_path: Path,
) -> None:
    stack, state = FakeStack(), tmp_path / "demo_users.json"

    code = await do_run(stack, state)

    saved = json.loads(state.read_text())
    assert set(saved["users"]) == set(ROLES)
    assert {u["email"] for u in saved["users"].values()} == set(stack.users)
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert len(saved["password"]) >= 16
    assert code == 0


async def test_the_admin_user_has_a_fixed_id_so_bootstrap_admin_sub_is_known_in_advance(
    tmp_path: Path,
) -> None:
    stack, state = FakeStack(), tmp_path / "demo_users.json"
    await do_run(stack, state)
    stack2, state2 = FakeStack(), tmp_path / "second.json"
    await do_run(stack2, state2)

    first = json.loads(state.read_text())["users"]["admin"]["id"]
    second = json.loads(state2.read_text())["users"]["admin"]["id"]
    assert first == second


async def test_running_twice_is_safe_and_keeps_the_same_users_and_password(
    tmp_path: Path,
) -> None:
    stack, state = FakeStack(), tmp_path / "demo_users.json"
    await do_run(stack, state)
    before = json.loads(state.read_text())

    code = await do_run(stack, state)

    assert json.loads(state.read_text()) == before
    assert len(stack.users) == 3
    assert code == 0


async def test_an_existing_user_gets_the_recorded_password_back(tmp_path: Path) -> None:
    stack, state = FakeStack(), tmp_path / "demo_users.json"
    stack.users["demo-customer@example.com"] = {"id": str(uuid.uuid4()), "password": "old"}

    await do_run(stack, state)

    password = json.loads(state.read_text())["password"]
    assert stack.users["demo-customer@example.com"]["password"] == password


async def test_when_the_admin_profile_is_already_admin_the_agent_is_promoted(
    tmp_path: Path,
) -> None:
    stack, state = FakeStack(), tmp_path / "demo_users.json"

    code = await do_run(stack, state)

    saved = json.loads(state.read_text())["users"]
    assert stack.profile_roles[saved["agent"]["id"]] == "agent"
    assert stack.profile_roles[saved["customer"]["id"]] == "customer"
    assert code == 0


async def test_when_the_admin_is_not_admin_yet_it_says_what_to_do_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stack, state = FakeStack(admin_role="customer"), tmp_path / "demo_users.json"

    code = await do_run(stack, state)

    out = capsys.readouterr().out
    admin_id = json.loads(state.read_text())["users"]["admin"]["id"]
    assert code == 3
    assert f"BOOTSTRAP_ADMIN_SUB={admin_id}" in out


async def test_neither_the_password_nor_tokens_nor_keys_are_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stack, state = FakeStack(), tmp_path / "demo_users.json"

    await do_run(stack, state)

    out = capsys.readouterr().out
    password = json.loads(state.read_text())["password"]
    assert password not in out
    assert "token-for-" not in out
    assert ADMIN_KEY not in out
    assert PUBLISHABLE not in out


async def test_a_given_password_is_used_instead_of_a_generated_one(tmp_path: Path) -> None:
    stack, state = FakeStack(), tmp_path / "demo_users.json"

    await do_run(stack, state, password=OWN_CHOICE)

    assert json.loads(state.read_text())["password"] == OWN_CHOICE
