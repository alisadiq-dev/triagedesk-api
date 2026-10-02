"""End-to-end demo of the TriageDesk API with the local demo users.

  eval "$(supabase status -o env | sed 's/^/export SUPABASE_/')"
  PYTHONPATH=. .venv/bin/python -m scripts.demo [--api-url URL] [--pause 1] [--rate-limit]

Needs the API (default http://127.0.0.1:8080, the Nginx URL of the local production stack), the
local Supabase stack, and the demo users (scripts/create_demo_users.py writes
supabase/demo_users.json). It creates ONE ticket (tickets are never deleted), works it through its
whole life as customer, agent and admin, and prints what each role sees. Never prints tokens, keys
or the demo password. Sends a normal User-Agent.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2

DEMO_FILE = Path("supabase/demo_users.json")
USER_AGENT = "Mozilla/5.0 (compatible; triagedesk-demo/1.0)"
API = "/api/v1"
Say = Callable[[str], None]
Json = dict[str, Any]


class DemoError(Exception):
    """The API did not answer the way the demo expects."""


class Session:
    """Calls the API as one role and checks the status, so a drifting API stops the demo loudly."""

    def __init__(self, client: httpx2.AsyncClient, tokens: dict[str, str]) -> None:
        self._client = client
        self._tokens = tokens

    async def call(
        self,
        role: str,
        method: str,
        path: str,
        expect: int,
        what: str,
        json: Json | None = None,
        params: dict[str, str | int] | None = None,
    ) -> Json:
        response = await self._client.request(
            method,
            path,
            json=json,
            params=params,
            headers={"Authorization": f"Bearer {self._tokens[role]}"},
        )
        if response.status_code != expect:
            raise DemoError(
                f"{what}: {method} {path} as {role} gave {response.status_code}, expected {expect}"
            )
        body: Json | list[Any] = response.json()
        return body if isinstance(body, dict) else {"items": body}


def _fields(data: Json, keys: list[str]) -> str:
    return ", ".join(f"{key}={data.get(key)!r}" for key in keys)


async def _wait_for_triage(session: Session, path: str, wait_seconds: float) -> Json:
    deadline = time.monotonic() + wait_seconds
    while True:
        ticket = await session.call("admin", "GET", path, 200, "read the ticket")
        if ticket["ai_status"] != "pending" or time.monotonic() > deadline:
            return ticket
        await asyncio.sleep(0.5)


async def run_demo(
    client: httpx2.AsyncClient,
    tokens: dict[str, str],
    say: Say,
    pause: float = 1.0,
    wait_seconds: float = 30.0,
    show_rate_limit: bool = False,
) -> None:
    s = Session(client, tokens)

    async def step(title: str) -> None:
        say(f"\n== {title}")
        await asyncio.sleep(pause)

    await step("1. Who is who (the role comes from the database, never from the token)")
    for role in ("customer", "agent", "admin"):
        say(f"    {role}: role={(await s.call(role, 'GET', f'{API}/me', 200, 'me'))['role']}")

    await step("2. Admin: categories and SLA policies")
    categories = await s.call("admin", "GET", f"{API}/categories", 200, "categories")
    say("    categories: " + ", ".join(c["name"] for c in categories["items"]))
    policies = await s.call("admin", "GET", f"{API}/sla-policies", 200, "policies")
    say("    SLA (response/resolution hours): " + ", ".join(
        f"{p['priority']}={p['response_hours']}/{p['resolution_hours']}" for p in policies["items"]
    ))  # fmt: skip

    await step("3. Customer opens a ticket (title and description only)")
    created = await s.call(
        "customer", "POST", f"{API}/tickets", 201, "create ticket",
        json={
            "title": "I was charged twice for my March invoice",
            "description": "My card was charged twice for invoice 1042. Please refund one payment.",
        },
    )  # fmt: skip
    path = f"{API}/tickets/{created['id']}"
    say(f"    customer sees exactly: {sorted(created)}")

    await step("4. AI triage runs in the background (keyword fallback without a model key)")
    ticket = await _wait_for_triage(s, path, wait_seconds)
    say(
        "    staff see: "
        + _fields(ticket, ["ai_status", "priority", "priority_source", "sentiment"])
    )
    other = await s.call("agent", "GET", path, 200, "agent reads the ticket")
    say(f"    AI draft hidden from an agent who is not the assignee: "
        f"{other.get('ai_suggested_reply') is None}")  # fmt: skip

    await step("5. Agent claims the ticket and starts work")
    await s.call("agent", "POST", f"{path}/claim", 200, "claim")
    mine = await s.call("agent", "GET", path, 200, "assignee reads the ticket")
    draft = (
        "yes"
        if mine.get("ai_suggested_reply")
        else "none (no model key: the fallback writes no draft)"
    )
    say(f"    the assignee sees the AI draft: {draft}")
    status = f"{path}/status"
    await s.call("agent", "POST", status, 200, "start work", json={"status": "in_progress"})

    await step("6. Internal note, public reply (first response), and what the customer sees")
    comments = f"{path}/comments"
    note = {"body": "Checked billing: duplicate charge confirmed.", "is_internal": True}
    await s.call("agent", "POST", comments, 201, "internal note", json=note)
    reply = {"body": "Sorry about that. We refunded the duplicate payment."}
    await s.call("agent", "POST", comments, 201, "public reply", json=reply)
    seen = await s.call("customer", "GET", comments, 200, "customer reads comments")
    authors = [c["author_type"] for c in seen["items"]]
    say(f"    customer sees {seen['total']} comment(s) by {authors}: no notes, no names")
    sla = await s.call("agent", "GET", f"{path}/sla", 200, "sla")
    say("    SLA now: " + _fields(sla, ["first_response_breached", "resolution_breached"]))

    await step("7. Waiting on the customer, who answers; workflow rules")
    await s.call("agent", "POST", status, 200, "wait", json={"status": "waiting_on_customer"})
    thanks = {"body": "Thank you, I see the refund."}
    await s.call("customer", "POST", comments, 201, "customer answer", json=thanks)
    await s.call("agent", "POST", status, 200, "back to work", json={"status": "in_progress"})
    await s.call("agent", "POST", status, 200, "resolve", json={"status": "resolved"})
    bad = await s.call(
        "agent", "POST", status, 409, "invalid transition", json={"status": "waiting_on_customer"}
    )
    say(f"    invalid transition refused: {bad['error']['code']}: {bad['error']['message']}")
    await s.call("agent", "POST", status, 200, "reopen", json={"status": "in_progress"})
    await s.call("agent", "POST", status, 200, "resolve again", json={"status": "resolved"})
    await s.call("agent", "POST", status, 200, "close", json={"status": "closed"})
    late = await s.call("customer", "POST", comments, 409, "late comment", json={"body": "More"})
    say(f"    a closed ticket takes no comments: {late['error']['code']}")

    await step("8. Permissions: what each role may not do")
    denied = await s.call("customer", "GET", f"{API}/users", 403, "customer on an admin route")
    say(f"    customer on GET /users: {denied['error']['code']}")
    nobody = f"{API}/tickets/00000000-0000-0000-0000-000000000000"
    missing = await s.call("customer", "GET", nobody, 404, "unknown ticket")
    say(f"    a ticket you may not see looks like it does not exist: {missing['error']['code']}")
    hidden = await s.call(
        "customer", "GET", f"{API}/tickets", 403, "hidden filter", params={"priority": "high"}
    )
    say(f"    customers cannot filter on fields they cannot see: {hidden['error']['code']}")

    await step("9. Admin: filters, search and the audit trail")
    found = await s.call(
        "admin", "GET", f"{API}/tickets", 200, "search", params={"q": "invoice", "status": "closed"}
    )
    say(f"    closed tickets matching 'invoice': {found['total']}")
    events = await s.call(
        "admin", "GET", f"{path}/events", 200, "audit trail", params={"page_size": 100}
    )
    say("    audit trail: " + " -> ".join(e["event_type"] for e in events["items"]))

    if show_rate_limit:
        await step("10. Rate limit: invalid ticket bodies count too (no ticket is created)")
        codes: list[int] = []
        headers = {"Authorization": f"Bearer {tokens['customer']}"}
        for _ in range(12):
            response = await client.post(f"{API}/tickets", json={}, headers=headers)
            codes.append(response.status_code)
        say(f"    status codes: {codes}")
        if 429 not in codes:
            raise DemoError("expected a 429 after the per-user ticket limit")
        say("    429 rate_limited with Retry-After; the limit is per user, per minute")
    say("\nDemo finished.")


async def _tokens(supabase_url: str, key: str) -> dict[str, str]:
    demo = json.loads(DEMO_FILE.read_text())
    tokens: dict[str, str] = {}
    async with httpx2.AsyncClient(
        base_url=supabase_url, headers={"User-Agent": USER_AGENT}, timeout=15
    ) as auth:
        for role, user in demo["users"].items():
            response = await auth.post(
                "/auth/v1/token",
                params={"grant_type": "password"},
                json={"email": user["email"], "password": demo["password"]},
                headers={"apikey": key},
            )
            response.raise_for_status()
            tokens[role] = str(response.json()["access_token"])
    return tokens


async def main() -> int:
    default_url = os.environ.get("API_BASE_URL", "http://127.0.0.1:8080")
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=default_url)
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--rate-limit", action="store_true", help="also show the 429 (last)")
    args = parser.parse_args()
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    if not key or not DEMO_FILE.exists():
        print("needs SUPABASE_PUBLISHABLE_KEY and supabase/demo_users.json (see the docstring)")
        return 2
    tokens = await _tokens(os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321"), key)
    async with httpx2.AsyncClient(
        base_url=args.api_url, headers={"User-Agent": USER_AGENT}, timeout=30
    ) as client:
        try:
            await run_demo(client, tokens, print, args.pause, show_rate_limit=args.rate_limit)
        except DemoError as exc:
            print(f"\nDEMO STOPPED: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
