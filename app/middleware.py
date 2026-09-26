"""Response headers that harden every page the app serves.

The policy below is derived from what the templates actually load, not from a
copied snippet. A CSP that is wrong by one directive does not warn anybody: it
silently stops a script loading and the Mini App looks broken. So the rule for
changing it is that tests/test_review_findings.py parses the templates and
fails if they reference an origin the policy does not allow.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("tm_pro.middleware")

# Telegram serves the Mini App SDK from its own domain. Everything else the
# app loads - scripts, styles, fonts - is local, and the only remote-looking
# images are the data: URIs that static/style.css uses for its chevrons and
# the decorative circle.
TELEGRAM_SDK = "https://telegram.org"

CSP = "; ".join(
    (
        "default-src 'self'",
        f"script-src 'self' {TELEGRAM_SDK}",
        "style-src 'self'",
        "font-src 'self'",
        # data: is required: static/style.css inlines two SVGs as
        # background-image, which the img-src directive governs.
        "img-src 'self' data:",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'self'",
        "object-src 'none'",
    )
)

# Deliberately NOT set: X-Frame-Options and frame-ancestors.
#
# A Telegram Mini App is loaded inside Telegram's own frame. Denying framing
# would be the textbook clickjacking control and would also stop the product
# from opening at all on the web and desktop clients. Naming the Telegram
# origins instead was considered and rejected: the set differs across clients
# and versions, and getting it wrong fails closed on someone's phone where
# nobody would see a console warning. The honest position is that this app
# cannot use framing denial, not that the header was forgotten.
BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Permitted-Cross-Domain-Policies": "none",
    "Cross-Origin-Resource-Policy": "same-site",
    # The app uses none of these; a script that got in could not either (Batch 27, L2).
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}

# Only meaningful over TLS, and actively harmful on a local http:// run because
# the browser would then refuse plain http for the whole host for a year.
HSTS = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds the headers above to every response.

    The policy comes from settings so that it can be relaxed or switched off
    through an environment variable on the host, without a code change and
    without a deploy. An empty CONTENT_SECURITY_POLICY sends no CSP at all:
    that escape hatch exists because a broken Mini App is worse than a missing
    header, and the person who needs it will be looking at a blank screen.
    """

    def __init__(self, app, policy: str = CSP, hsts: bool = True):
        super().__init__(app)
        self.policy = policy
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        for name, value in BASE_HEADERS.items():
            response.headers.setdefault(name, value)

        if self.policy:
            response.headers.setdefault("Content-Security-Policy", self.policy)

        if self.hsts and request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", HSTS)

        return response


def effective_csp(settings) -> str:
    """The policy to send. An override from CONTENT_SECURITY_POLICY replaces the
    strict default, so it is logged at start-up rather than left silent (L3)."""
    override = getattr(settings, "content_security_policy", None)
    if override is None:
        return CSP
    detail = "switches the CSP off" if not override.strip() else f"replaces the default: {override}"
    logger.warning("CONTENT_SECURITY_POLICY %s", detail, extra={"event": "csp_override"})
    return override


class _TooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="TOO_LARGE")


class BodySizeLimitMiddleware:
    """Refuses a request body over max_bytes before it is read whole (M1).

    A Content-Length over the limit is answered at once. Otherwise the bytes are
    counted as they arrive, so a streamed body without that header stops at the
    limit too: the endpoint's read fails with a 413, which FastAPI passes on.
    """

    def __init__(self, app, max_bytes: int = 65536):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await JSONResponse({"detail": "TOO_LARGE"}, status_code=413)(scope, receive, send)
            return
        seen = 0

        async def counted():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise _TooLarge()
            return message

        await self.app(scope, counted, send)
