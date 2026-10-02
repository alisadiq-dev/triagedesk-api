import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.jwt_auth import JwksUnavailableError, JwtTokenVerifier
from app.core.security import AuthenticationError, AuthServiceUnavailableError
from tests.support.jwt_helpers import (
    AUDIENCE,
    ISSUER,
    KID,
    USER_ID,
    StaticKeys,
    claims,
    forge_hs256,
    new_key,
    sign,
)

KEY = new_key()


class BrokenKeys:
    async def get_key(self, kid: str) -> None:
        raise JwksUnavailableError("jwks fetch failed: ConnectError")


def verifier(keys: Any = None) -> JwtTokenVerifier:
    provider = keys if keys is not None else StaticKeys(**{KID: KEY.public_key()})
    return JwtTokenVerifier(provider, issuer=ISSUER, audience=AUDIENCE)


async def test_valid_token_returns_the_user_without_any_role() -> None:
    user = await verifier().verify(sign(KEY, claims(role="admin")))

    assert user.id == uuid.UUID(USER_ID)
    assert user.email == "ada@example.com"
    assert not hasattr(user, "role")


async def test_token_without_an_email_claim_is_accepted() -> None:
    user = await verifier().verify(sign(KEY, claims(email=None)))

    assert user.email is None


@pytest.mark.parametrize("flag", [None, False])
async def test_is_anonymous_absent_or_false_is_accepted(flag: bool | None) -> None:
    payload = claims() if flag is None else claims(is_anonymous=flag)

    assert (await verifier().verify(sign(KEY, payload))).id == uuid.UUID(USER_ID)


def invalid_tokens() -> dict[str, str]:
    other_key = new_key()
    pem = KEY.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {
        "expired": sign(KEY, claims(exp=1)),
        "wrong-audience": sign(KEY, claims(aud="someone-else")),
        "wrong-issuer": sign(KEY, claims(iss="https://evil.example/auth/v1")),
        "signed-by-another-key": sign(other_key, claims()),
        "unknown-kid": sign(KEY, claims(), kid="not-in-jwks"),
        "missing-kid": sign(KEY, claims(), kid=None),
        "missing-exp": sign(KEY, claims(exp=None)),
        "missing-sub": sign(KEY, claims(sub=None)),
        "missing-aud": sign(KEY, claims(aud=None)),
        "missing-iss": sign(KEY, claims(iss=None)),
        "sub-not-a-uuid": sign(KEY, claims(sub="not-a-uuid")),
        "anonymous-user": sign(KEY, claims(is_anonymous=True)),
        "anonymous-as-string": sign(KEY, claims(is_anonymous="false")),
        "hs256-with-public-key-as-secret": forge_hs256(claims(), pem),
        "alg-none": jwt.encode(claims(), "", algorithm="none", headers={"kid": KID}),
        "not-before-in-the-future": sign(KEY, claims(nbf=int(time.time()) + 3600)),
        "issued-far-in-the-future": sign(KEY, claims(iat=int(time.time()) + 3600)),
        "audience-list-without-ours": sign(KEY, claims(aud=["someone-else", "another"])),
        "empty-audience": sign(KEY, claims(aud="")),
        "empty-sub": sign(KEY, claims(sub="")),
        "anonymous-null": sign(KEY, claims(is_anonymous=0)),
        "es384-header": jwt.encode(
            claims(),
            ec.generate_private_key(ec.SECP384R1()),
            algorithm="ES384",
            headers={"kid": KID},
        ),
        "garbage": "not.a.jwt",
        "empty": "",
    }


@pytest.mark.parametrize("name", sorted(invalid_tokens()))
async def test_invalid_tokens_are_rejected(name: str) -> None:
    with pytest.raises(AuthenticationError):
        await verifier().verify(invalid_tokens()[name])


async def test_every_invalid_token_gets_the_identical_error_message() -> None:
    messages = set()
    for token in invalid_tokens().values():
        with pytest.raises(AuthenticationError) as caught:
            await verifier().verify(token)
        messages.add((caught.value.status_code, caught.value.code, caught.value.message))

    assert len(messages) == 1


async def test_wrong_algorithm_is_rejected_before_any_key_lookup() -> None:
    keys = StaticKeys(**{KID: KEY.public_key()})

    with pytest.raises(AuthenticationError):
        await verifier(keys).verify(forge_hs256(claims(), b"x" * 32))

    assert keys.lookups == []


async def test_unreachable_jwks_is_a_503_not_a_401() -> None:
    with pytest.raises(AuthServiceUnavailableError) as caught:
        await verifier(BrokenKeys()).verify(sign(KEY, claims()))

    assert caught.value.status_code == 503
    assert caught.value.code == "auth_unavailable"
    assert caught.value.headers["Retry-After"]


async def test_small_clock_skew_between_issuer_and_api_is_tolerated() -> None:
    soon = int(time.time()) + 5

    user = await verifier().verify(sign(KEY, claims(iat=soon, nbf=soon)))

    assert user.id == uuid.UUID(USER_ID)


async def test_audience_list_containing_ours_is_accepted() -> None:
    user = await verifier().verify(sign(KEY, claims(aud=[AUDIENCE, "another"])))

    assert user.id == uuid.UUID(USER_ID)
