"""Localhost hardening for a single-user app.

- Host header must be a loopback host (blocks DNS rebinding).
- Origin, when present, must be an allowed loopback origin (blocks cross-site requests).
- Every /api request must carry the per-installation token (X-AFN-Token), which the browser
  receives once via the launch URL. Other local processes without the token get 401.
- No state-changing GET endpoints exist; mutations are POST/PUT/PATCH/DELETE.
"""

from __future__ import annotations

import hmac
import os
import re
from urllib.parse import urlparse

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


MAX_BODY_BYTES = 60 * 1024 * 1024
# Everything is served from this origin; blob: is for agenda images fetched with the token. No framing (clickjacking).
CSP = (
    "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)


def _host_only(value: str) -> str:
    host = value.strip()
    if host.startswith("["):
        bracket, _, rest = host.partition("]")
        return bracket + "]" if rest == "" or re.fullmatch(r":\d+", rest) else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def host_allowed(host_header: str | None) -> bool:
    return bool(host_header) and _host_only(host_header or "") in LOOPBACK


def origin_allowed(origin: str | None, extra: set[str]) -> bool:
    if origin is None or origin == "null":
        return origin is None
    u = urlparse(origin)
    return (u.hostname or "") in {"127.0.0.1", "localhost", "::1"} or origin in extra


class LocalOnlyMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware): the size-limited receive must reach the route directly, so an
    oversized chunked body surfaces as a 413 HTTPException rather than being wrapped in a task-group error."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token
        self.extra_origins = {o for o in os.environ.get("AFN_DEV_ORIGINS", "").split(",") if o}

    def refuse(self, request: Request) -> Response | None:
        if not host_allowed(request.headers.get("host")):
            return JSONResponse({"detail": "Host not allowed"}, status_code=400)
        if not origin_allowed(request.headers.get("origin"), self.extra_origins):
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
        path = request.url.path
        if path.startswith("/api/") and path != "/api/health":
            supplied = request.headers.get("x-afn-token", "").encode("utf-8", "replace")
            if not hmac.compare_digest(supplied, self.token.encode("utf-8")):
                return JSONResponse(
                    {"detail": "Missing or invalid API token. Open the app via the URL printed at launch."},
                    status_code=401,
                )
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        seen = 0

        async def limited() -> Message:
            # Content-Length can be absent (chunked); count the bytes that actually arrive.
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > MAX_BODY_BYTES:
                raise HTTPException(413, "Request body too large")
            return message

        async def send_hardened(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("Cache-Control", "no-store")
                headers.setdefault("Content-Security-Policy", CSP)
            await send(message)

        refusal = self.refuse(Request(scope))
        await (refusal or self.app)(scope, limited, send_hardened)
