import asyncio
import json
import logging
from collections.abc import Callable

import httpx2
import pytest

from app.core.jwt_auth import JwksProvider, JwksUnavailableError
from tests.support.jwt_helpers import KID, jwks_document, new_key, public_jwk

URL = "http://127.0.0.1:54321/auth/v1/.well-known/jwks.json"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Endpoint:
    """A fake JWKS endpoint that counts requests."""

    def __init__(self) -> None:
        self.calls = 0
        self.body: object = jwks_document(public_jwk(new_key()))
        self.status = 200
        self.error: Exception | None = None

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        if self.error is not None:
            raise self.error
        content = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        return httpx2.Response(self.status, content=content)


def provider(endpoint: Endpoint, clock: Clock) -> JwksProvider:
    return JwksProvider(
        URL,
        timeout_seconds=1,
        cache_seconds=300,
        min_refetch_seconds=30,
        transport=httpx2.MockTransport(endpoint),
        clock=clock,
    )


async def test_known_key_is_returned_and_cached() -> None:
    endpoint, clock = Endpoint(), Clock()
    keys = provider(endpoint, clock)

    first = await keys.get_key(KID)
    second = await keys.get_key(KID)

    assert first is not None
    assert first is second
    assert endpoint.calls == 1


async def test_unknown_kid_returns_none() -> None:
    assert await provider(Endpoint(), Clock()).get_key("other") is None


async def test_unknown_kid_does_not_trigger_a_refetch_on_every_call() -> None:
    endpoint, clock = Endpoint(), Clock()
    keys = provider(endpoint, clock)

    for _ in range(5):
        await keys.get_key("attacker-chosen-kid")

    assert endpoint.calls == 1


async def test_unknown_kid_refetches_after_the_minimum_interval_to_pick_up_rotation() -> None:
    endpoint, clock = Endpoint(), Clock()
    keys = provider(endpoint, clock)
    await keys.get_key(KID)
    endpoint.body = jwks_document(public_jwk(new_key(), kid="rotated"))
    clock.now += 31

    assert await keys.get_key("rotated") is not None
    assert endpoint.calls == 2


async def test_cache_expires_and_the_set_is_refetched() -> None:
    endpoint, clock = Endpoint(), Clock()
    keys = provider(endpoint, clock)
    await keys.get_key(KID)
    clock.now += 301

    await keys.get_key(KID)

    assert endpoint.calls == 2


async def test_concurrent_first_requests_fetch_the_key_set_once() -> None:
    endpoint, clock = Endpoint(), Clock()
    keys = provider(endpoint, clock)

    await asyncio.gather(*(keys.get_key(KID) for _ in range(10)))

    assert endpoint.calls == 1


async def test_only_es256_ec_keys_are_accepted() -> None:
    endpoint, clock = Endpoint(), Clock()
    good, wrong_alg = new_key(), new_key()
    endpoint.body = jwks_document(
        public_jwk(good, kid="good"),
        {**public_jwk(wrong_alg, kid="wrong-alg"), "alg": "RS256"},
        {"kty": "oct", "kid": "symmetric", "k": "c2VjcmV0"},
    )
    keys = provider(endpoint, clock)

    assert await keys.get_key("good") is not None
    assert await keys.get_key("wrong-alg") is None
    assert await keys.get_key("symmetric") is None


def break_with_server_error(endpoint: Endpoint) -> None:
    endpoint.status = 500


def break_with_invalid_json(endpoint: Endpoint) -> None:
    endpoint.body = b"not json"


def break_with_wrong_shape(endpoint: Endpoint) -> None:
    endpoint.body = {"keys": "nope"}


def break_with_no_keys(endpoint: Endpoint) -> None:
    endpoint.body = {"keys": []}


def break_with_timeout(endpoint: Endpoint) -> None:
    endpoint.error = httpx2.ConnectTimeout("slow")


def break_with_refused_connection(endpoint: Endpoint) -> None:
    endpoint.error = httpx2.ConnectError("refused")


@pytest.mark.parametrize(
    "break_endpoint",
    [
        break_with_server_error,
        break_with_invalid_json,
        break_with_wrong_shape,
        break_with_no_keys,
        break_with_timeout,
        break_with_refused_connection,
    ],
)
async def test_unreachable_or_invalid_jwks_fails_closed(
    break_endpoint: Callable[[Endpoint], None],
) -> None:
    endpoint, clock = Endpoint(), Clock()
    break_endpoint(endpoint)

    with pytest.raises(JwksUnavailableError):
        await provider(endpoint, clock).get_key(KID)


async def test_expired_cache_is_not_served_when_the_refetch_fails() -> None:
    endpoint, clock = Endpoint(), Clock()
    keys = provider(endpoint, clock)
    await keys.get_key(KID)
    clock.now += 301
    endpoint.status = 503

    with pytest.raises(JwksUnavailableError):
        await keys.get_key(KID)


async def test_after_a_failure_requests_fail_fast_instead_of_hitting_the_network_again() -> None:
    endpoint, clock = Endpoint(), Clock()
    endpoint.status = 500
    keys = provider(endpoint, clock)

    for _ in range(3):
        with pytest.raises(JwksUnavailableError):
            await keys.get_key(KID)

    assert endpoint.calls == 1


async def test_the_endpoint_is_retried_once_the_backoff_has_passed() -> None:
    endpoint, clock = Endpoint(), Clock()
    endpoint.status = 500
    keys = provider(endpoint, clock)
    with pytest.raises(JwksUnavailableError):
        await keys.get_key(KID)
    endpoint.status = 200
    clock.now += 31

    await keys.get_key("any")

    assert endpoint.calls == 2


async def test_redirects_are_not_followed() -> None:
    def redirect(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"Location": "http://evil.example/jwks.json"})

    keys = JwksProvider(
        URL,
        timeout_seconds=1,
        cache_seconds=300,
        min_refetch_seconds=30,
        transport=httpx2.MockTransport(redirect),
    )

    with pytest.raises(JwksUnavailableError):
        await keys.get_key(KID)


async def test_an_oversized_response_is_rejected() -> None:
    endpoint, clock = Endpoint(), Clock()
    endpoint.body = json.dumps(endpoint.body).encode() + b" " * (200 * 1024)

    with pytest.raises(JwksUnavailableError):
        await provider(endpoint, clock).get_key(KID)


async def test_private_other_curve_and_non_signing_keys_are_ignored() -> None:
    endpoint, clock = Endpoint(), Clock()
    leaked = new_key()
    private_entry = {**public_jwk(leaked, kid="private"), "d": "AAAA"}
    p384 = {**public_jwk(new_key(), kid="p384"), "crv": "P-384"}
    encryption_only = public_jwk(new_key(), kid="enc", use="enc")
    endpoint.body = jwks_document(
        public_jwk(new_key(), kid="good"), private_entry, p384, encryption_only
    )
    keys = provider(endpoint, clock)

    assert await keys.get_key("good") is not None
    for kid in ("private", "p384", "enc"):
        assert await keys.get_key(kid) is None


async def test_an_outage_is_logged_once_not_once_per_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    endpoint, clock = Endpoint(), Clock()
    endpoint.status = 500
    keys = provider(endpoint, clock)

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        for _ in range(5):
            with pytest.raises(JwksUnavailableError):
                await keys.get_key(KID)

    assert caplog.text.count("jwks_unavailable") == 1
    assert "status 500" in caplog.text
