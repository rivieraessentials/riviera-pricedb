"""Transport security (owner security points 4 & 5): a strict Content-Security-Policy and
hardening headers on every response, plus an Origin / JSON guard on state-changing requests.

CSP allows this origin only: scripts and styles are self-hosted (SheetJS included, with SRI),
there are no inline <script> blocks or inline on* handlers, and `connect-src 'self'` keeps the
page from talking to anything but this server. The Origin guard rejects a cross-site
state-changing request (classic CSRF) and requires any request body to be JSON — a cross-site
HTML form cannot send application/json, and a browser cannot forge the Origin header.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Inline style="" attributes come from the ported prototype, so style-src needs
# 'unsafe-inline'; script-src deliberately does NOT (no inline scripts anywhere).
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

_SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, allowed_origins: set[str], max_body_bytes: int = 16 * 1024 * 1024):
        super().__init__(app)
        self.allowed = {o.rstrip("/") for o in allowed_origins}
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request, call_next):
        clen = request.headers.get("content-length")
        if clen is not None and clen.isdigit() and int(clen) > self.max_body_bytes:
            return self._deny(413, "request body too large")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin is not None and origin.rstrip("/") not in self.allowed:
                return self._deny(403, "cross-origin request refused (Origin not allowed)")
            ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
            has_body = clen not in (None, "", "0")
            if has_body and ctype and ctype != "application/json":
                return self._deny(415, "request body must be application/json")
        resp = await call_next(request)
        for k, v in _SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp

    def _deny(self, code: int, msg: str) -> JSONResponse:
        resp = JSONResponse({"detail": msg}, status_code=code)
        for k, v in _SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp
