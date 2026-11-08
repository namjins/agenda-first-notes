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
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _host_only(value: str) -> str:
    host = value.strip()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def host_allowed(host_header: str | None) -> bool:
    return bool(host_header) and _host_only(host_header or "") in LOOPBACK


def origin_allowed(origin: str | None, extra: set[str]) -> bool:
    if origin is None or origin == "null":
        return origin is None
    u = urlparse(origin)
    return (u.hostname or "") in {"127.0.0.1", "localhost", "::1"} or origin in extra


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.token = token
        self.extra_origins = {o for o in os.environ.get("AFN_DEV_ORIGINS", "").split(",") if o}

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def,override]
        if not host_allowed(request.headers.get("host")):
            return JSONResponse({"detail": "Host not allowed"}, status_code=400)
        if not origin_allowed(request.headers.get("origin"), self.extra_origins):
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        path = request.url.path
        if path.startswith("/api/") and path != "/api/health":
            supplied = request.headers.get("x-afn-token", "")
            if not hmac.compare_digest(supplied, self.token):
                return JSONResponse(
                    {"detail": "Missing or invalid API token. Open the app via the URL printed at launch."},
                    status_code=401,
                )
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp
