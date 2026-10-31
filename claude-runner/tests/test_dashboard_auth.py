"""Tests for dashboard_auth."""

from __future__ import annotations

import logging

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from claude_runner.dashboard_auth import (
    SESSION_COOKIE,
    AuthGuard,
    login_routes,
)
from claude_runner.dashboard_auth_config import DashboardAuthSettings
from claude_runner.dashboard_auth_crypto import hash_password
from claude_runner.dashboard_auth_limit import LoginLimiter

# Made-up values used throughout the tests.
USERNAME = "test-user"
PASSWORD = "s3cr3t-Pw!"
TOKEN = "test-token-made-up"
SECRET = "test-session-secret"

_SECRET_VALUES = (USERNAME, PASSWORD, TOKEN, SECRET)

_PASSWORD_HASH = hash_password(PASSWORD)


class FakeClock:
    """Injectable clock returning a mutable ``now`` (seconds, float)."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def auth_settings() -> DashboardAuthSettings:
    return DashboardAuthSettings(
        username=USERNAME,
        password_hash=_PASSWORD_HASH,
        token=TOKEN,
        session_secret=SECRET,
    )


# ---- test-app helpers ----


async def _healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _api_test(request: Request) -> JSONResponse:
    return JSONResponse({"data": "protected"})


async def _index(request: Request) -> HTMLResponse:
    return HTMLResponse("<h1>Dashboard</h1>")


def make_app(
    settings: DashboardAuthSettings | None = None,
    limiter: LoginLimiter | None = None,
) -> Starlette:
    settings = settings if settings is not None else auth_settings()
    limiter = limiter if limiter is not None else LoginLimiter()
    routes = [
        Route("/", endpoint=_index),
        Route("/healthz", endpoint=_healthz),
        Route("/api/test", endpoint=_api_test),
        *login_routes(settings, limiter),
    ]
    middleware = [Middleware(AuthGuard, settings=settings)]
    return Starlette(routes=routes, middleware=middleware)


# ---------------------------------------------------------- AC1: auth disabled


def test_auth_disabled_passes_through() -> None:
    settings = DashboardAuthSettings()
    app = make_app(settings=settings)
    client = TestClient(app, follow_redirects=False)
    r = client.get("/api/test")
    assert r.status_code == 200
    assert r.json() == {"data": "protected"}


# ---------------------------------------------------------- AC2: no credentials


def test_html_get_without_credentials_redirects() -> None:
    app = make_app()
    client = TestClient(app, follow_redirects=False)
    r = client.get("/", headers={"accept": "text/html"})
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_api_get_without_credentials_returns_401() -> None:
    app = make_app()
    client = TestClient(app, follow_redirects=False)
    r = client.get("/api/test")
    assert r.status_code == 401


def test_healthz_is_open() -> None:
    app = make_app()
    client = TestClient(app, follow_redirects=False)
    r = client.get("/healthz")
    assert r.status_code == 200


def test_login_get_is_open() -> None:
    app = make_app()
    client = TestClient(app, follow_redirects=False)
    r = client.get("/login")
    assert r.status_code == 200
    assert "{{error}}" not in r.text


def test_logout_is_open() -> None:
    app = make_app()
    client = TestClient(app, follow_redirects=False)
    r = client.get("/logout")
    assert r.status_code == 303


# ---------------------------------------------------------- AC3: password login


def test_right_password_sets_cookie() -> None:
    app = make_app()
    client = TestClient(app, follow_redirects=False)
    r = client.post("/login", data={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 303
    cookie = r.headers.get("set-cookie")
    assert cookie is not None
    assert SESSION_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    # session cookie should now grant access
    r2 = client.get("/api/test")
    assert r2.status_code == 200


def test_wrong_password_no_cookie() -> None:
    app = make_app()
    client = TestClient(app, follow_redirects=False)
    r = client.post("/login", data={"username": USERNAME, "password": "wrong"})
    assert r.status_code == 401
    assert r.headers.get("set-cookie") is None


def test_wrong_user_and_wrong_password_bodies_identical() -> None:
    app = make_app()
    client = TestClient(app, follow_redirects=False)
    r1 = client.post("/login", data={"username": "nobody", "password": PASSWORD})
    r2 = client.post("/login", data={"username": USERNAME, "password": "wrong"})
    assert r1.status_code == 401
    assert r2.status_code == 401
    assert r1.text == r2.text


# ---------------------------------------------------------- AC4: bearer / token


def test_right_bearer_token_accepted() -> None:
    app = make_app()
    client = TestClient(app, follow_redirects=False)
    r = client.get("/api/test", headers={"authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_wrong_bearer_token_rejected() -> None:
    app = make_app()
    client = TestClient(app, follow_redirects=False)
    r = client.get("/api/test", headers={"authorization": "Bearer wrong-token"})
    assert r.status_code == 401


def test_token_on_login_form_gives_session() -> None:
    app = make_app()
    client = TestClient(app, follow_redirects=False)
    r = client.post("/login", data={"token": TOKEN})
    assert r.status_code == 303
    assert r.headers.get("set-cookie") is not None
    # session cookie should now grant access
    r2 = client.get("/api/test")
    assert r2.status_code == 200


# ---------------------------------------------------------- AC5: rate limiting


def test_repeated_failures_gives_429() -> None:
    clock = FakeClock()
    limiter = LoginLimiter(now=clock)
    app = make_app(limiter=limiter)
    client = TestClient(app, follow_redirects=False)
    # 5 free attempts + 1 that triggers lockout, all return 401
    for _ in range(6):
        r = client.post("/login", data={"username": USERNAME, "password": "wrong"})
        assert r.status_code == 401
    # 7th attempt is rate-limited
    r = client.post("/login", data={"username": USERNAME, "password": "wrong"})
    assert r.status_code == 429
    assert r.headers.get("retry-after") is not None


# ---------------------------------------------------------- AC6: no secret logs


def test_no_secrets_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    app = make_app()
    client = TestClient(app, follow_redirects=False)
    with caplog.at_level(logging.INFO):
        client.post("/login", data={"username": USERNAME, "password": "wrong"})
        client.post("/login", data={"username": USERNAME, "password": PASSWORD})
        client.post("/login", data={"token": TOKEN})
        client.get("/api/test")
        client.get("/api/test", headers={"authorization": "Bearer wrong-token"})
    for record in caplog.records:
        text = record.getMessage()
        for secret in _SECRET_VALUES:
            assert secret not in text, f"secret {secret!r} leaked in log: {text}"
