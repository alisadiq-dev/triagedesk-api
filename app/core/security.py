import uuid
from dataclasses import dataclass
from typing import Protocol

from app.core.errors import AppError


class AuthenticationError(AppError):
    """401: no token, or a token that is invalid, expired or not meant for this API.

    The body is always identical so callers cannot tell why a token was rejected.
    """

    status_code = 401
    code = "unauthorized"
    headers = {"WWW-Authenticate": "Bearer"}

    def __init__(self) -> None:
        super().__init__("Invalid or missing credentials")


class AuthServiceUnavailableError(AppError):
    """503: tokens cannot be checked right now (signing keys unreachable). Fails closed."""

    status_code = 503
    code = "auth_unavailable"
    headers = {"Retry-After": "30"}

    def __init__(self) -> None:
        super().__init__("Authentication service unavailable")


@dataclass(frozen=True)
class AuthenticatedUser:
    """Identity proven by a verified token. The role is NOT here: it comes from profiles."""

    id: uuid.UUID
    email: str | None


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> AuthenticatedUser:
        """Return the user for a valid token, or raise AuthenticationError."""
        ...
