"""Small ASGI security controls applied before request routing."""

from __future__ import annotations

import asyncio
import re
import time
from uuid import uuid4

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, clear_contextvars

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestTooLargeError(Exception):
    """Raised internally when a streamed request exceeds its configured budget."""


class RequestBodyLimitMiddleware:
    """Reject oversized bodies even when Transfer-Encoding is chunked."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                if int(raw_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                await self._reject(send, status=400, detail="Invalid Content-Length")
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestTooLargeError:
            await self._reject(send)

    @staticmethod
    async def _reject(
        send: Send, *, status: int = 413, detail: str = "Request body is too large"
    ) -> None:
        body = ('{"detail":"' + detail + '"}').encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class RequestLoggingMiddleware:
    """Emit one JSON event per request without logging secrets or query strings."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = structlog.get_logger("spectral_api.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        candidate = headers.get(b"x-request-id", b"").decode("ascii", errors="ignore")
        request_id = candidate if _SAFE_REQUEST_ID.fullmatch(candidate) else str(uuid4())
        started = time.perf_counter()
        status_code = 500
        clear_contextvars()
        bind_contextvars(request_id=request_id)

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                response_headers = list(message.get("headers", []))
                response_headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            self.logger.exception(
                "http_request_failed",
                method=scope.get("method"),
                path=scope.get("path"),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise
        else:
            self.logger.info(
                "http_request_completed",
                method=scope.get("method"),
                path=scope.get("path"),
                status_code=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        finally:
            clear_contextvars()


class RequestTimeoutMiddleware:
    """Cancel control-plane requests that exceed their runtime budget."""

    def __init__(self, app: ASGIApp, timeout_seconds: float) -> None:
        self.app = app
        self.timeout_seconds = timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self.app(scope, receive, tracked_send)
        except TimeoutError:
            if response_started:
                raise
            body = b'{"detail":"Request timed out"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 504,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    """Attach browser hardening headers to every HTTP response."""

    def __init__(self, app: ASGIApp, *, deployed: bool) -> None:
        self.app = app
        self.deployed = deployed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                csp = (
                    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
                    if self.deployed
                    else "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                    "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
                )
                headers.extend(
                    [
                        (b"content-security-policy", csp.encode()),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                        (b"cross-origin-resource-policy", b"same-site"),
                    ]
                )
                if self.deployed:
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RateLimitMiddleware:
    """Bound abusive bursts per client; production should add a shared gateway limit."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        general_limit: int,
        upload_limit: int,
        window_seconds: int,
    ) -> None:
        self.app = app
        self.general_limit = general_limit
        self.upload_limit = upload_limit
        self.window_seconds = window_seconds
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        method = str(scope.get("method", "GET"))
        upload_request = "/uploads" in path and method not in {"GET", "HEAD", "OPTIONS"}
        bucket = "upload" if upload_request else "general"
        limit = self.upload_limit if upload_request else self.general_limit
        client_value = scope.get("client")
        client = str(client_value[0]) if client_value else "unknown"
        now = time.monotonic()
        key = (client, bucket)
        async with self._lock:
            started, count = self._windows.get(key, (now, 0))
            if now - started >= self.window_seconds:
                started, count = now, 0
            count += 1
            self._windows[key] = (started, count)
            if len(self._windows) > 10_000:
                cutoff = now - self.window_seconds
                self._windows = {
                    item_key: value
                    for item_key, value in self._windows.items()
                    if value[0] >= cutoff
                }
        if count > limit:
            body = b'{"detail":"Rate limit exceeded"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"retry-after", str(self.window_seconds).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
