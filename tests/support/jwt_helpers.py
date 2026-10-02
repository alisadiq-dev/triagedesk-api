"""Helpers to mint ES256 tokens and JWKS documents with a local test key."""

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

ISSUER = "http://127.0.0.1:54321/auth/v1"
AUDIENCE = "authenticated"
KID = "test-key-1"
USER_ID = "5f0c1e2a-8c0e-4a53-9a41-2f6f1f0b7d11"


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def public_jwk(key: ec.EllipticCurvePrivateKey, kid: str = KID, **extra: str) -> dict[str, Any]:
    jwk: dict[str, Any] = jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {**jwk, "kid": kid, "alg": "ES256", "use": "sig", **extra}


def jwks_document(*jwks: dict[str, Any]) -> dict[str, Any]:
    return {"keys": list(jwks)}


def claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    base: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": USER_ID,
        "email": "ada@example.com",
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    base.update(overrides)
    return {name: value for name, value in base.items() if value is not None}


def sign(
    key: Any, payload: dict[str, Any], *, kid: str | None = KID, algorithm: str = "ES256"
) -> str:
    headers = {} if kid is None else {"kid": kid}
    return jwt.encode(payload, key, algorithm=algorithm, headers=headers)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def forge_hs256(payload: dict[str, Any], secret: bytes, *, kid: str = KID) -> str:
    """Build an HS256 token by hand (PyJWT refuses to sign HS256 with a public-key PEM)."""
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    body = _b64(json.dumps(payload).encode())
    signature = hmac.new(secret, f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64(signature)}"
