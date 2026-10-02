import json
import logging

import httpx2
import pytest
from fastapi import FastAPI

from app.core.logging import JsonFormatter, RequestIdMiddleware, request_id_var


def format_record(**extra: object) -> dict[str, object]:
    record = logging.LogRecord("app.audit", logging.INFO, __file__, 1, "role_changed", (), None)
    for name, value in extra.items():
        setattr(record, name, value)
    entry: dict[str, object] = json.loads(JsonFormatter().format(record))
    return entry


def test_json_formatter_emits_one_json_object_with_extras_and_request_id() -> None:
    token = request_id_var.set("req-123")
    try:
        entry = format_record(actor_id="a", target_id="t", old_role="agent", new_role="admin")
    finally:
        request_id_var.reset(token)

    assert entry["message"] == "role_changed"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "app.audit"
    assert entry["request_id"] == "req-123"
    assert entry["actor_id"] == "a"
    assert entry["new_role"] == "admin"
    assert "timestamp" in entry


def build_client() -> httpx2.AsyncClient:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/id")
    async def current() -> dict[str, str | None]:
        return {"request_id": request_id_var.get()}

    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test")


async def test_a_request_id_is_generated_and_echoed() -> None:
    async with build_client() as client:
        response = await client.get("/id")

    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert len(response.headers["x-request-id"]) == 32


async def test_a_safe_incoming_request_id_is_kept() -> None:
    async with build_client() as client:
        response = await client.get("/id", headers={"X-Request-ID": "abc-123"})

    assert response.headers["x-request-id"] == "abc-123"
    assert response.json()["request_id"] == "abc-123"


@pytest.mark.parametrize("bad", ["has space", "x" * 65, "new\\nline", "<script>"])
async def test_an_unsafe_incoming_request_id_is_replaced(bad: str) -> None:
    async with build_client() as client:
        response = await client.get("/id", headers={"X-Request-ID": bad})

    assert response.headers["x-request-id"] != bad
    assert len(response.headers["x-request-id"]) == 32


def test_http_client_loggers_are_quiet_so_request_urls_stay_out_of_the_logs() -> None:
    from app.core.logging import configure_logging

    configure_logging()

    for name in ("httpx", "httpx2", "httpcore", "google_genai"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
