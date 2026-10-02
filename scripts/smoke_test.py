"""Smoke test against the Nginx URL of the local production stack (not part of the unit tests).

  PYTHONPATH=. .venv/bin/python -m scripts.smoke_test [--base-url URL] [--signed-in]

Checks what only shows up through the real gateway: the API answers, the docs are hidden, errors
use the API format, security headers are present, an oversized body is refused in the API format,
and a spoofed X-Forwarded-For does not get around the rate limit. The rate-limit check goes last
because it uses up this client's public budget for up to a minute (--skip-rate-limit-check leaves
it out).

--signed-in also runs a short flow with the demo users (supabase/demo_users.json and
SUPABASE_PUBLISHABLE_KEY): a customer creates a ticket, staff see it, the customer cannot call
admin routes, and triage leaves `pending`. It leaves one ticket titled "[smoke test]" behind
(tickets are never deleted). Sends a normal User-Agent. Never prints tokens, keys or the demo
password.
"""

import argparse
import asyncio
import json
import os
import secrets
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx2

USER_AGENT = "Mozilla/5.0 (compatible; triagedesk-smoke-test/1.0)"
DEMO_FILE = Path("supabase/demo_users.json")
DEFAULT_BURST = 130  # a little more than the default public limit of 120 per minute


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str = ""


class CheckFailed(Exception):
    pass


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def _is_api_error(response: httpx2.Response, status: int, code: str) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    return (
        response.status_code == status
        and isinstance(body, dict)
        and isinstance(body.get("error"), dict)
        and body["error"].get("code") == code
    )


Check = Callable[[httpx2.AsyncClient], Awaitable[None]]


async def _health(client: httpx2.AsyncClient) -> None:
    response = await client.get("/health")
    _expect(response.status_code == 200 and response.json() == {"status": "ok"}, "not 200 ok")


async def _ready(client: httpx2.AsyncClient) -> None:
    response = await client.get("/ready")
    _expect(response.status_code == 200, f"status {response.status_code} (database down?)")
    _expect(response.json() == {"status": "ready"}, "unexpected body")


def _docs_check(path: str) -> Check:
    async def check(client: httpx2.AsyncClient) -> None:
        response = await client.get(path)
        _expect(response.status_code == 404, f"{path} answered {response.status_code}")

    return check


async def _unknown_path(client: httpx2.AsyncClient) -> None:
    _expect(_is_api_error(await client.get("/no/such/path"), 404, "not_found"), "wrong shape")


async def _unauthorized(client: httpx2.AsyncClient) -> None:
    response = await client.get("/api/v1/me")
    _expect(_is_api_error(response, 401, "unauthorized"), "wrong status or shape")
    _expect("www-authenticate" in response.headers, "no WWW-Authenticate header")


async def _security_headers(client: httpx2.AsyncClient) -> None:
    headers = (await client.get("/health")).headers
    _expect(headers.get("x-content-type-options") == "nosniff", "X-Content-Type-Options")
    _expect(headers.get("cache-control") == "no-store", "Cache-Control")
    _expect("frame-ancestors 'none'" in headers.get("content-security-policy", ""), "CSP")
    server = headers.get("server", "")
    _expect(not any(ch.isdigit() for ch in server), f"Server header shows a version: {server}")


async def _oversized_body(client: httpx2.AsyncClient) -> None:
    response = await client.post("/api/v1/tickets", content=b"x" * 70_000)
    _expect(_is_api_error(response, 413, "payload_too_large"), f"status {response.status_code}")


def _forwarded_for_check(burst: int) -> Check:
    async def check(client: httpx2.AsyncClient) -> None:
        limited = 0
        for _ in range(burst):
            fake = ".".join(str(1 + secrets.randbelow(254)) for _ in range(4))
            response = await client.get(
                "/health", headers={"X-Forwarded-For": fake, "X-Real-IP": fake}
            )
            limited += response.status_code == 429
        _expect(limited > 0, f"no 429 in {burst} requests: a client-supplied address was trusted")

    return check


async def run_checks(
    client: httpx2.AsyncClient, rate_limit_burst: int = DEFAULT_BURST
) -> list[Result]:
    checks: list[tuple[str, Check]] = [
        ("/health answers 200", _health),
        ("/ready answers 200 (database reachable)", _ready),
        ("/docs is not exposed", _docs_check("/docs")),
        ("/redoc is not exposed", _docs_check("/redoc")),
        ("/openapi.json is not exposed", _docs_check("/openapi.json")),
        ("unknown paths give the API error format", _unknown_path),
        ("401 without a token has the API error format", _unauthorized),
        ("security headers present, no server version", _security_headers),
        ("oversized body: 413 in the API error format", _oversized_body),
    ]
    if rate_limit_burst > 0:  # last: it spends this client's public rate limit budget
        checks.append(
            (
                "spoofed X-Forwarded-For does not bypass the rate limit",
                _forwarded_for_check(rate_limit_burst),
            )
        )
    results: list[Result] = []
    for name, check in checks:
        try:
            await check(client)
            results.append(Result(name, True))
        except CheckFailed as exc:
            results.append(Result(name, False, str(exc)))
        except httpx2.HTTPError as exc:
            results.append(Result(name, False, f"{type(exc).__name__}: gateway not reachable?"))
    return results


# --- optional signed-in flow (needs the local Supabase stack and the demo users) ----------------


async def _token(auth: httpx2.AsyncClient, key: str, email: str, password: str) -> str:
    response = await auth.post(
        "/auth/v1/token",
        params={"grant_type": "password"},
        json={"email": email, "password": password},
        headers={"apikey": key},
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


async def signed_in_flow(client: httpx2.AsyncClient, supabase_url: str, key: str) -> list[Result]:
    demo = json.loads(DEMO_FILE.read_text())
    results: list[Result] = []
    async with httpx2.AsyncClient(
        base_url=supabase_url, headers={"User-Agent": USER_AGENT}, timeout=15
    ) as auth:
        tokens = {
            role: await _token(auth, key, user["email"], demo["password"])
            for role, user in demo["users"].items()
        }

    def bearer(role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens[role]}"}

    async def step(name: str, action: Callable[[], Awaitable[None]]) -> None:
        try:
            await action()
            results.append(Result(name, True))
        except (CheckFailed, httpx2.HTTPError, KeyError) as exc:
            results.append(Result(name, False, f"{type(exc).__name__}: {exc}"))

    ticket: dict[str, str] = {}

    async def create() -> None:
        response = await client.post(
            "/api/v1/tickets",
            json={"title": "[smoke test]", "description": "Created by scripts/smoke_test.py"},
            headers=bearer("customer"),
        )
        _expect(response.status_code == 201, f"status {response.status_code}")
        _expect(
            set(response.json())
            == {"id", "title", "description", "status", "created_at", "updated_at"},
            "customer response is not the allowlist",
        )
        ticket["id"] = response.json()["id"]

    async def customer_forbidden() -> None:
        _expect(
            (await client.get("/api/v1/users", headers=bearer("customer"))).status_code == 403,
            "not 403",
        )

    async def admin_sees_it() -> None:
        response = await client.get(f"/api/v1/tickets/{ticket['id']}", headers=bearer("admin"))
        _expect(response.status_code == 200, f"status {response.status_code}")
        _expect("priority" in response.json(), "no staff fields for the admin")

    async def triage_leaves_pending() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = await client.get(f"/api/v1/tickets/{ticket['id']}", headers=bearer("admin"))
            if response.json().get("ai_status") in ("completed", "failed"):
                return
            await asyncio.sleep(1)
        raise CheckFailed("ai_status is still pending after 30 s")

    await step("signed in: customer creates a ticket (allowlist response)", create)
    if "id" in ticket:
        await step("signed in: customer is refused on an admin route", customer_forbidden)
        await step("signed in: admin sees the ticket with staff fields", admin_sees_it)
        await step(
            "signed in: triage leaves pending (background task works)", triage_leaves_pending
        )
    return results


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url", default=os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8080")
    )
    parser.add_argument("--signed-in", action="store_true")
    parser.add_argument("--skip-rate-limit-check", action="store_true")
    args = parser.parse_args()
    async with httpx2.AsyncClient(
        base_url=args.base_url, headers={"User-Agent": USER_AGENT}, timeout=15
    ) as client:
        # The signed-in flow goes first so it is not caught by the budget spent in the last check.
        results: list[Result] = []
        if args.signed_in:
            key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
            if not key or not DEMO_FILE.exists():
                print("--signed-in needs SUPABASE_PUBLISHABLE_KEY and supabase/demo_users.json")
                return 2
            supabase_url = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321")
            results += await signed_in_flow(client, supabase_url, key)
        burst = 0 if args.skip_rate_limit_check else DEFAULT_BURST
        results += await run_checks(client, rate_limit_burst=burst)
    for result in results:
        print(
            f"{'PASS' if result.ok else 'FAIL'}  {result.name}"
            + (f"  ({result.detail})" if result.detail else "")
        )
    failures = [r for r in results if not r.ok]
    print(
        f"\n{len(results) - len(failures)} passed, {len(failures)} failed against {args.base_url}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
