from collections.abc import Callable

import httpx2

from scripts.smoke_test import Result, run_checks

NOT_FOUND = {"error": {"code": "not_found", "message": "Not found"}}
SECURITY = {
    "x-content-type-options": "nosniff",
    "cache-control": "no-store",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
}


class Gateway:
    """A fake Nginx plus API. Flags switch on one defect at a time."""

    def __init__(
        self,
        expose_docs: bool = False,
        trust_forwarded_for: bool = False,
        plain_413: bool = False,
        missing_headers: bool = False,
    ) -> None:
        self.expose_docs = expose_docs
        self.trust_forwarded_for = trust_forwarded_for
        self.plain_413 = plain_413
        self.missing_headers = missing_headers
        self.seen: dict[str, int] = {}

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        headers = {} if self.missing_headers else dict(SECURITY)
        if path in ("/docs", "/redoc", "/openapi.json"):
            if self.expose_docs:
                return httpx2.Response(200, json={}, headers=headers)
            return httpx2.Response(404, json=NOT_FOUND, headers=headers)
        if path == "/health":
            key = (
                request.headers.get("x-forwarded-for", "client")
                if self.trust_forwarded_for
                else "client"
            )
            self.seen[key] = self.seen.get(key, 0) + 1
            if self.seen[key] > 120:
                return httpx2.Response(
                    429,
                    json={"error": {"code": "rate_limited", "message": "x"}},
                    headers={**headers, "Retry-After": "30"},
                )
            return httpx2.Response(200, json={"status": "ok"}, headers=headers)
        if path == "/ready":
            return httpx2.Response(200, json={"status": "ready"}, headers=headers)
        if path == "/api/v1/me":
            return httpx2.Response(
                401,
                json={
                    "error": {"code": "unauthorized", "message": "Invalid or missing credentials"}
                },
                headers={**headers, "WWW-Authenticate": "Bearer"},
            )
        if path == "/api/v1/tickets" and request.method == "POST" and len(request.content) > 65536:
            if self.plain_413:
                return httpx2.Response(413, text="<html>413</html>")
            return httpx2.Response(
                413,
                json={"error": {"code": "payload_too_large", "message": "x"}},
                headers=headers,
            )
        return httpx2.Response(404, json=NOT_FOUND, headers=headers)


async def results_for(gateway: Callable[[httpx2.Request], httpx2.Response]) -> list[Result]:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(gateway), base_url="http://nginx"
    ) as client:
        return await run_checks(client, rate_limit_burst=130)


def failed(results: list[Result]) -> list[str]:
    return [r.name for r in results if not r.ok]


async def test_a_correct_gateway_passes_every_check() -> None:
    results = await results_for(Gateway())

    assert failed(results) == []
    assert len(results) >= 8


async def test_exposed_docs_fail_the_docs_check() -> None:
    results = await results_for(Gateway(expose_docs=True))

    assert any("docs" in name for name in failed(results))


async def test_a_trusted_forwarded_for_header_fails_the_spoofing_check() -> None:
    results = await results_for(Gateway(trust_forwarded_for=True))

    assert any("forwarded" in name.lower() for name in failed(results))


async def test_a_plain_nginx_413_page_fails_the_error_format_check() -> None:
    results = await results_for(Gateway(plain_413=True))

    assert any("413" in name for name in failed(results))


async def test_missing_security_headers_fail() -> None:
    results = await results_for(Gateway(missing_headers=True))

    assert any("header" in name.lower() for name in failed(results))


async def test_the_rate_limit_check_runs_last_and_can_be_skipped() -> None:
    gateway = Gateway()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(gateway), base_url="http://nginx"
    ) as client:
        results = await run_checks(client, rate_limit_burst=0)

    assert not any("forwarded" in r.name.lower() for r in results)
    assert gateway.seen.get("client", 0) < 120


async def test_an_unreachable_gateway_fails_instead_of_crashing() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    results = await results_for(refuse)

    assert failed(results)  # reported as failures, not an exception
