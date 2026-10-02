import io
import json
import logging
from collections.abc import Iterator

import httpx2
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.main import create_app

URL = "postgresql+asyncpg://user:pw@localhost:5432/triagedesk"
TICKET_ID = "7f1c2b9e-1234-4abc-9def-0123456789ab"


def make_client(**overrides: object) -> httpx2.AsyncClient:
    settings = Settings(_env_file=None, database_url=SecretStr(URL)).model_copy(update=overrides)
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=create_app(settings)), base_url="http://test"
    )


class AccessLog:
    """Captures what the access logger prints, as the JSON the app would write."""

    def __init__(self) -> None:
        self.stream = io.StringIO()

    @property
    def raw(self) -> str:
        return self.stream.getvalue()

    def entries(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.raw.splitlines()]


@pytest.fixture
def access_log() -> Iterator[AccessLog]:
    capture = AccessLog()
    handler = logging.StreamHandler(capture.stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app.access")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield capture
    finally:
        logger.removeHandler(handler)


async def test_one_line_per_request_with_the_route_template_status_and_duration(
    access_log: AccessLog,
) -> None:
    async with make_client() as client:
        await client.get("/health")

    [entry] = access_log.entries()
    assert entry["message"] == "request"
    assert (entry["method"], entry["route"], entry["status"]) == ("GET", "/health", 200)
    assert isinstance(entry["duration_ms"], int)


async def test_the_query_string_never_reaches_the_log(access_log: AccessLog) -> None:
    async with make_client() as client:
        await client.get("/health", params={"q": "secret-search-term", "token": "secret-token"})

    assert "secret" not in access_log.raw
    assert "?" not in access_log.raw


async def test_path_parameters_are_logged_as_the_template_not_the_value(
    access_log: AccessLog,
) -> None:
    async with make_client() as client:
        response = await client.get(f"/api/v1/tickets/{TICKET_ID}")

    assert response.status_code == 401
    [entry] = access_log.entries()
    assert entry["route"] == "/api/v1/tickets/{ticket_id}"
    assert TICKET_ID not in access_log.raw


async def test_an_unknown_path_is_logged_as_unmatched_without_its_text(
    access_log: AccessLog,
) -> None:
    async with make_client() as client:
        await client.get("/no/such/thing-with-secret-in-it", params={"x": "1"})

    [entry] = access_log.entries()
    assert (entry["route"], entry["status"]) == ("unmatched", 404)
    assert "secret" not in access_log.raw


async def test_refused_requests_are_logged_too(access_log: AccessLog) -> None:
    async with make_client(max_request_body_bytes=10, rate_limit_public_per_minute=1) as client:
        await client.post("/api/v1/tickets", content=b"x" * 11)
        await client.get("/health")
        await client.get("/health")

    statuses = [entry["status"] for entry in access_log.entries()]
    assert statuses == [413, 200, 429]


async def test_the_line_carries_the_request_id_of_the_response(access_log: AccessLog) -> None:
    async with make_client() as client:
        response = await client.get("/health", headers={"X-Request-ID": "req-42"})

    [entry] = access_log.entries()
    assert response.headers["X-Request-ID"] == "req-42"
    assert entry["request_id"] == "req-42"


async def test_headers_and_bodies_are_not_logged(access_log: AccessLog) -> None:
    async with make_client() as client:
        await client.post(
            "/api/v1/tickets",
            json={"title": "SECRET-TITLE"},
            headers={"Authorization": "Bearer secret-bearer-token"},
        )

    assert "SECRET-TITLE" not in access_log.raw
    assert "secret-bearer-token" not in access_log.raw
