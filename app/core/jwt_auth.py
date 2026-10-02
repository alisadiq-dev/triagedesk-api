"""Supabase JWT verification: ES256 only, keys from the issuer's JWKS, fail closed."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.config import Settings
from app.core.security import AuthenticatedUser, AuthenticationError, AuthServiceUnavailableError

logger = logging.getLogger("app.auth")

ALGORITHM = "ES256"
REQUIRED_CLAIMS = ["exp", "sub", "iss", "aud"]


class JwksUnavailableError(Exception):
    """The signing keys could not be fetched or were invalid. Messages never hold secrets."""


class KeyProvider(Protocol):
    async def get_key(self, kid: str) -> ec.EllipticCurvePublicKey | None: ...


class JwksProvider:
    """Fetches and caches the issuer's public keys.

    - every fetch has a timeout; failures raise JwksUnavailableError (callers fail closed)
    - keys are cached for cache_seconds; an expired cache is never served
    - an unknown kid triggers at most one refetch per min_refetch_seconds (key rotation without
      letting random kids hammer the endpoint)
    - after a failure, requests fail fast for min_refetch_seconds instead of re-trying the network
    """

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        cache_seconds: int,
        min_refetch_seconds: int,
        transport: httpx2.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._cache_seconds = cache_seconds
        self._min_refetch_seconds = min_refetch_seconds
        self._clock = clock
        self._client = httpx2.AsyncClient(timeout=timeout_seconds, transport=transport)
        self._keys: dict[str, ec.EllipticCurvePublicKey] = {}
        self._fetched_at: float | None = None
        self._failed_at: float | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_key(self, kid: str) -> ec.EllipticCurvePublicKey | None:
        key = self._cached(kid)
        if key is not None:
            return key
        async with self._lock:
            key = self._cached(kid)
            if key is not None:
                return key
            if self._should_fetch():
                await self._refresh()
            return self._cached(kid)

    def _cache_age(self) -> float | None:
        return None if self._fetched_at is None else self._clock() - self._fetched_at

    def _cache_is_valid(self) -> bool:
        age = self._cache_age()
        return age is not None and age < self._cache_seconds

    def _cached(self, kid: str) -> ec.EllipticCurvePublicKey | None:
        return self._keys.get(kid) if self._cache_is_valid() else None

    def _should_fetch(self) -> bool:
        if (
            self._failed_at is not None
            and self._clock() - self._failed_at < self._min_refetch_seconds
        ):
            raise JwksUnavailableError("jwks fetch skipped during backoff after a failure")
        age = self._cache_age()
        return age is None or age >= self._cache_seconds or age >= self._min_refetch_seconds

    async def _refresh(self) -> None:
        try:
            keys = await self._fetch()
        except JwksUnavailableError:
            self._failed_at = self._clock()
            raise
        self._keys = keys
        self._fetched_at = self._clock()
        self._failed_at = None

    async def _fetch(self) -> dict[str, ec.EllipticCurvePublicKey]:
        try:
            response = await self._client.get(self._url)
        except httpx2.HTTPError as exc:
            raise JwksUnavailableError(f"jwks fetch failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise JwksUnavailableError(f"jwks fetch failed: status {response.status_code}")
        try:
            document = response.json()
        except ValueError as exc:
            raise JwksUnavailableError("jwks response is not valid JSON") from exc
        entries = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(entries, list):
            raise JwksUnavailableError("jwks response has no key list")
        keys: dict[str, ec.EllipticCurvePublicKey] = {}
        for entry in entries:
            parsed = _parse_signing_key(entry)
            if parsed is not None:
                keys[parsed[0]] = parsed[1]
        if not keys:
            raise JwksUnavailableError("jwks response has no usable ES256 keys")
        return keys


def _parse_signing_key(entry: Any) -> tuple[str, ec.EllipticCurvePublicKey] | None:
    """Accept only public ES256 (EC P-256) keys with a kid; ignore everything else."""
    if not isinstance(entry, dict):
        return None
    kid = entry.get("kid")
    if not isinstance(kid, str):
        return None
    acceptable = (
        entry.get("kty") == "EC"
        and entry.get("crv") == "P-256"
        and entry.get("alg") in (None, ALGORITHM)
        and "d" not in entry
    )
    if not acceptable:
        return None
    try:
        key = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(entry))
    except (jwt.PyJWTError, ValueError):
        return None
    return (kid, key) if isinstance(key, ec.EllipticCurvePublicKey) else None


class JwtTokenVerifier:
    def __init__(self, keys: KeyProvider, *, issuer: str, audience: str) -> None:
        self._keys = keys
        self._issuer = issuer
        self._audience = audience

    async def verify(self, token: str) -> AuthenticatedUser:
        kid = self._read_kid(token)
        try:
            key = await self._keys.get_key(kid)
        except JwksUnavailableError as exc:
            logger.warning("jwks_unavailable reason=%s", exc)
            raise AuthServiceUnavailableError from exc
        if key is None:
            raise AuthenticationError
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError:
            raise AuthenticationError from None
        return self._to_user(claims)

    @staticmethod
    def _read_kid(token: str) -> str:
        """Check the header before any network work; only ES256 tokens with a kid proceed."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthenticationError from None
        kid = header.get("kid")
        if header.get("alg") != ALGORITHM or not isinstance(kid, str) or not kid:
            raise AuthenticationError
        return kid

    @staticmethod
    def _to_user(claims: dict[str, Any]) -> AuthenticatedUser:
        if claims.get("is_anonymous", False) is not False:
            raise AuthenticationError
        try:
            user_id = uuid.UUID(claims["sub"])
        except (ValueError, TypeError, AttributeError):
            raise AuthenticationError from None
        email = claims.get("email")
        return AuthenticatedUser(id=user_id, email=email if isinstance(email, str) else None)

    async def aclose(self) -> None:
        close = getattr(self._keys, "aclose", None)
        if close is not None:
            await close()


def build_verifier(settings: Settings) -> JwtTokenVerifier:
    issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
    provider = JwksProvider(
        f"{issuer}/.well-known/jwks.json",
        timeout_seconds=settings.jwks_timeout_seconds,
        cache_seconds=settings.jwks_cache_seconds,
        min_refetch_seconds=settings.jwks_min_refetch_seconds,
    )
    return JwtTokenVerifier(provider, issuer=issuer, audience=settings.supabase_jwt_audience)
