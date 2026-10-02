"""Live check against the local Supabase stack (`supabase start`): not part of the test suite.

Signs up a throwaway user, then verifies the issued token with our JwtTokenVerifier and reports only
non-secret facts (algorithm, kid, aud, iss, claim names). Reads the publishable key from the
SUPABASE_PUBLISHABLE_KEY env var and never prints it or the token.
"""

import asyncio
import os
import sys
import uuid

import httpx2
import jwt
from pydantic import SecretStr

from app.core.config import Settings
from app.core.jwt_auth import build_verifier
from app.core.security import AuthServiceUnavailableError

USER_AGENT = "Mozilla/5.0 (compatible; triagedesk-live-check/1.0)"


async def main() -> int:
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    print(f"publishable key length: {len(key)}")
    if not key:
        print("SUPABASE_PUBLISHABLE_KEY is not set")
        return 2
    settings = Settings(
        _env_file=None, database_url=SecretStr("postgresql+asyncpg://unused/unused")
    )
    base = settings.supabase_url.rstrip("/")
    headers = {"apikey": key, "User-Agent": USER_AGENT}
    email = f"live-check-{uuid.uuid4().hex[:8]}@example.com"
    async with httpx2.AsyncClient(timeout=10, headers=headers) as client:
        jwks = (await client.get(f"{base}/auth/v1/.well-known/jwks.json")).json()
        print(
            "jwks keys:",
            [(k.get("kty"), k.get("crv"), k.get("alg"), k.get("kid")) for k in jwks["keys"]],
        )
        response = await client.post(
            f"{base}/auth/v1/signup", json={"email": email, "password": "live-check-password-1"}
        )
        print("signup status:", response.status_code)
        token = response.json().get("access_token", "")
    print(f"token length: {len(token)}")
    header = jwt.get_unverified_header(token)
    payload = jwt.decode(token, options={"verify_signature": False})
    print("header alg/typ/kid:", header.get("alg"), header.get("typ"), header.get("kid"))
    print("aud:", payload.get("aud"), "| iss:", payload.get("iss"))
    print("claim names:", sorted(payload))
    print(
        "is_anonymous:",
        payload.get("is_anonymous", "<absent>"),
        "| role claim:",
        payload.get("role"),
    )
    verifier = build_verifier(settings)
    try:
        user = await verifier.verify(token)
    finally:
        await verifier.aclose()
    print(
        "verified by JwtTokenVerifier: sub matches =",
        str(user.id) == payload["sub"],
        "| email =",
        user.email,
    )
    unreachable = build_verifier(settings.model_copy(update={"supabase_url": "http://127.0.0.1:1"}))
    try:
        await unreachable.verify(token)
        print("unreachable JWKS: UNEXPECTEDLY ACCEPTED the token")
        return 1
    except AuthServiceUnavailableError:
        print("unreachable JWKS: fails closed with AuthServiceUnavailableError (503)")
    finally:
        await unreachable.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
