import uuid
from dataclasses import dataclass
from typing import Protocol

from app.core.errors import AppError


class AuthenticationError(AppError):
    """401: no token, or a token that is invalid, expired or not meant for this API."""

    status_code = 401
    code = "unauthorized"
    headers = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True)
class AuthenticatedUser:
    """Identity proven by a verified token. The role is NOT here: it comes from profiles."""

    id: uuid.UUID
    email: str | None


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> AuthenticatedUser:
        """Return the user for a valid token, or raise AuthenticationError."""
        ...
