from collections.abc import AsyncIterator

import httpx2
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.hardening import STRICT_CSP
from app.main import create_app

URL = "postgresql+asyncpg://user:pw@localhost:5432/triagedesk"


def make_client(**overrides: object) -> httpx2.AsyncClient:
    settings = Settings(_env_file=None, database_url=SecretStr(URL)).model_copy(update=overrides)
    app = create_app(settings)
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test")


async def chunks(total: int, size: int = 1024) -> AsyncIterator[bytes]:
    sent = 0
    while sent < total:
        piece = min(size, total - sent)
        yield b"x" * piece
        sent += piece


# --- request body limit ----------------------------------------------------------------------


async def test_a_declared_body_over_the_limit_is_refused_with_413_in_the_error_format() -> None:
    async with make_client(max_request_body_bytes=100) as client:
        response = await client.post("/api/v1/tickets", content=b"x" * 101)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_a_body_exactly_at_the_limit_is_accepted_by_the_size_check() -> None:
    async with make_client(max_request_body_bytes=100) as client:
        response = await client.post("/api/v1/tickets", content=b"x" * 100)

    assert response.status_code == 401  # got past the size check; no token


async def test_a_chunked_body_over_the_limit_is_refused_too() -> None:
    async with make_client(max_request_body_bytes=2048) as client:
        response = await client.post("/api/v1/tickets", content=chunks(10_000))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_a_chunked_body_under_the_limit_is_accepted_by_the_size_check() -> None:
    async with make_client(max_request_body_bytes=2048) as client:
        response = await client.post("/api/v1/tickets", content=chunks(1000))

    assert response.status_code == 401


async def test_the_default_limit_allows_the_largest_valid_ticket() -> None:
    biggest = b'{"title": "' + b"t" * 200 + b'", "description": "' + b"d" * 10_000 + b'"}'
    async with make_client() as client:
        response = await client.post("/api/v1/tickets", content=biggest)

    assert response.status_code == 401


# --- response headers ------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/health", "/api/v1/me", "/no/such/route"])
async def test_every_response_carries_the_security_headers(path: str) -> None:
    async with make_client() as client:
        response = await client.get(path)

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Content-Security-Policy"] == STRICT_CSP


async def test_error_responses_from_the_limiter_and_size_check_also_carry_them() -> None:
    async with make_client(max_request_body_bytes=10) as client:
        response = await client.post("/health", content=b"x" * 11)

    assert response.status_code == 413
    assert response.headers["X-Content-Type-Options"] == "nosniff"


async def test_the_interactive_docs_keep_working_without_the_strict_csp() -> None:
    async with make_client() as client:
        docs = await client.get("/docs")

    assert docs.status_code == 200
    assert "Content-Security-Policy" not in docs.headers
    assert docs.headers["X-Content-Type-Options"] == "nosniff"


# --- docs switch -----------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_the_docs_and_openapi_can_be_switched_off(path: str) -> None:
    async with make_client(api_docs_enabled=False) as client:
        response = await client.get(path)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_the_openapi_document_is_served_by_default() -> None:
    async with make_client() as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
