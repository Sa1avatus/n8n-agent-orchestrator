"""Authentication middleware and login/logout routes for the AHAWR dashboard.

Uses Starlette directly.  When ``auth_enabled`` is False the middleware is a
no-op and every request passes through unchanged.  When enabled it blocks
every path except GET /healthz, GET/POST /login, GET /logout, GET /manifest.json and
GET /icon.svg; all other requests need a valid session cookie or an
``Authorization: Bearer <token>`` header.  HTML GET requests without credentials receive a
303 redirect to /login; everything else receives a 401 JSON response.

Nothing here logs passwords, tokens, cookie values or Authorization headers —
only the client address and a generic outcome word are logged.
"""

from __future__ import annotations

import logging
import time
from importlib import resources
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route
from starlette.types import ASGIApp

from .dashboard_auth_config import DashboardAuthSettings, auth_enabled
from .dashboard_auth_crypto import (
    check_token,
    issue_session,
    read_session,
    session_cookie_attributes,
    verify_password,
)
from .dashboard_auth_limit import LoginLimiter

logger = logging.getLogger("claude_runner.dashboard_auth")

#: Name of the session cookie — single source of truth for both middleware and
#: routes.
SESSION_COOKIE = "ahawr_session"

#: HTTP header for the rate-limit ``Retry-After`` value.
RETRY_AFTER = "Retry-After"

#: Paths that are reachable without authentication, keyed by ``(method, path)``.
_OPEN_METHOD_PATHS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/healthz"),
        ("GET", "/login"),
        ("POST", "/login"),
        ("GET", "/logout"),
        ("GET", "/manifest.json"),
        ("GET", "/icon.svg"),
    }
)

#: Static login page HTML with a ``{{error}}`` placeholder.
_LOGIN_PAGE = resources.files("claude_runner").joinpath("dashboard_login.html").read_text("utf-8")


#: Methods that change state; refused in read-only mode.
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def request_is_readonly(settings: DashboardAuthSettings) -> bool:
    """True when DASHBOARD_READONLY is on: the dashboard refuses every write."""
    return settings.readonly


def _client_addr(request: Request) -> str:
    """Return the connecting client address, or ``"unknown"`` if absent."""
    host = request.client.host if request.client is not None else ""
    return host or "unknown"


def _wants_html(request: Request) -> bool:
    """Return True when the client appears to want an HTML response."""
    accept = request.headers.get("accept", "")
    return "text/html" in accept


def _render_login_page(error: str = "", *, status_code: int = 200) -> HTMLResponse:
    """Render the login page, substituting ``{{error}}`` with *error*."""
    body = _LOGIN_PAGE.replace("{{error}}", error)
    return HTMLResponse(body, status_code=status_code)


def _session_ok(request: Request, settings: DashboardAuthSettings) -> bool:
    """Return True when the request carries a valid session cookie."""
    value = request.cookies.get(SESSION_COOKIE)
    if not value:
        return False
    now = int(time.time())
    subject = read_session(settings.session_secret, value, now)
    return subject is not None


def _bearer_ok(request: Request, settings: DashboardAuthSettings) -> bool:
    """Return True when the request carries a valid Bearer token."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return False
    given = auth[len("Bearer ") :]
    return bool(settings.token) and check_token(given, settings.token)


def _parse_form(body: bytes) -> dict[str, str]:
    """Parse ``application/x-www-form-urlencoded`` body into a flat dict.

    Only the first value of each field is kept; blank values are preserved.
    """
    fields = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {key: values[0] for key, values in fields.items()}


class AuthGuard(BaseHTTPMiddleware):
    """Middleware that enforces dashboard authentication.

    When ``settings`` is ``None`` or ``auth_enabled`` is False, every request
    passes through unchanged.
    """

    def __init__(self, app: ASGIApp, settings: DashboardAuthSettings | None = None) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self._settings is None:
            return await call_next(request)

        is_open = (request.method, request.url.path) in _OPEN_METHOD_PATHS
        if not is_open and request.method in _WRITE_METHODS and request_is_readonly(self._settings):
            return JSONResponse({"error": "dashboard is in read-only mode"}, status_code=403)

        if not auth_enabled(self._settings) or is_open:
            return await call_next(request)

        if _session_ok(request, self._settings) or _bearer_ok(request, self._settings):
            return await call_next(request)

        addr = _client_addr(request)
        logger.info("auth: denied from %s", addr)
        if _wants_html(request):
            return RedirectResponse(url="/login", status_code=303)
        logger.info("auth: 401 for %s", addr)
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)


def login_routes(settings: DashboardAuthSettings, limiter: LoginLimiter) -> list[Route]:
    """Return the GET /login, POST /login and GET /logout route objects."""

    async def login_get(request: Request) -> HTMLResponse:
        return _render_login_page()

    async def login_post(request: Request) -> Response:
        addr = _client_addr(request)

        retry = limiter.retry_after_seconds(addr)
        if retry > 0:
            logger.info("login: rate-limited from %s", addr)
            return PlainTextResponse(
                "Too many attempts",
                status_code=429,
                headers={RETRY_AFTER: str(int(retry))},
            )

        body = await request.body()
        form = _parse_form(body)
        username = form.get("username", "")
        password = form.get("password", "")
        given_token = form.get("token", "")

        ok = False
        subject = ""
        if given_token:
            ok = check_token(given_token, settings.token) if settings.token else False
            if ok:
                subject = "token"
        elif username and password:
            ok = (
                settings.username == username
                and bool(settings.password_hash)
                and verify_password(password, settings.password_hash)
            )
            if ok:
                subject = username

        if not ok:
            limiter.record_failure(addr)
            logger.info("login: failed from %s", addr)
            return _render_login_page("Invalid credentials", status_code=401)

        limiter.record_success(addr)
        logger.info("login: success from %s", addr)
        now = int(time.time())
        is_https = request.url.scheme == "https"
        value = issue_session(settings.session_secret, subject, now, settings.session_seconds)
        attrs = session_cookie_attributes(settings.session_seconds, is_https)
        response = RedirectResponse(url="/", status_code=303)
        response.headers["set-cookie"] = f"{SESSION_COOKIE}={value}; {attrs}"
        return response

    async def logout_get(request: Request) -> Response:
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        logger.info("auth: logout from %s", _client_addr(request))
        return response

    return [
        Route("/login", endpoint=login_get, methods=["GET"]),
        Route("/login", endpoint=login_post, methods=["POST"]),
        Route("/logout", endpoint=logout_get, methods=["GET"]),
    ]
