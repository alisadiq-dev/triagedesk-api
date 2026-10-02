"""Request body size limit and response security headers (pure ASGI middleware)."""

import json

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}
STRICT_CSP = "default-src 'none'; frame-ancestors 'none'"
# The interactive docs load their own scripts, so the strict policy would break them.
CSP_EXEMPT_PATHS = {"/docs", "/redoc"}

_TOO_LARGE_BODY = json.dumps(
    {"error": {"code": "payload_too_large", "message": "Request body is too large"}}
).encode()


class _BodyTooLargeError(Exception):
    pass


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        exempt_csp = scope["path"] in CSP_EXEMPT_PATHS

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if not exempt_csp:
                    headers.setdefault("Content-Security-Policy", STRICT_CSP)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodyLimitMiddleware:
    """413 for bodies over the limit: by Content-Length, and by counting chunked bodies."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = MutableHeaders(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._refuse(send)
            return

        received = 0
        exceeded = False
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    # The route fails to parse the body (nothing runs); we answer 413 below.
                    raise _BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if exceeded:
                if not response_started:
                    response_started = True
                    await self._refuse(send)
                return  # swallow whatever the app tried to send instead
            await send(message)

        await self.app(scope, counting_receive, guarded_send)

    @staticmethod
    async def _refuse(send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_TOO_LARGE_BODY)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})
