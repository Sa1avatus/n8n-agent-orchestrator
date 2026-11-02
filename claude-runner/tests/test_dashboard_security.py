"""End-to-end security tests on the real dashboard app (``create_dashboard_app``).

The app is built by the production factory and driven over ASGI. The runner is an
in-process transport and socket access is blocked, so nothing leaves the process.
Every HTTP route of the app is checked against the explicit public allowlist below:
any other route must refuse an unauthenticated request.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Route
from starlette.testclient import TestClient

from claude_runner.dashboard import DashboardSettings, create_dashboard_app
from claude_runner.dashboard_auth import SESSION_COOKIE
from claude_runner.dashboard_auth_crypto import hash_password, issue_session

# Made-up values; the test env is scrubbed of every real DASHBOARD_* / CLAUDE_RUNNER_* setting.
USERNAME = "test-user"
PASSWORD = "s3cr3t-Pw!"
TOKEN = "test-token-made-up"
SESSION_SECRET = "test-session-secret"
_PASSWORD_HASH = hash_password(PASSWORD)

BASE_URL = "http://localhost"
DASHBOARD_HEADER = {"X-AHAWR-Dashboard": "1"}
JSON_ACCEPT = {"Accept": "application/json"}
MUTATING_METHODS = ("POST", "PUT", "DELETE")
ENV_PREFIXES = ("DASHBOARD_", "CLAUDE_RUNNER_", "AHAWR_")
PLACEHOLDER = "placeholder"

# (method, path) -> status an unauthenticated request must get. Every other route of the app
# must answer 401 (JSON) or 303 (to /login) to an unauthenticated request.
PUBLIC_ROUTES: dict[tuple[str, str], int] = {
    ("GET", "/healthz"): 200,
    ("GET", "/login"): 200,
    ("POST", "/login"): 401,
    ("GET", "/logout"): 303,
    ("GET", "/manifest.json"): 200,
    ("GET", "/icon.svg"): 200,
}


def _refuse_network(*_args: Any, **_kwargs: Any) -> None:
    raise OSError("network access is blocked in tests")


def _refuse_runner(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, json={"detail": "runner is not reachable in tests"})


def _runner_answers_cancel(request: httpx.Request) -> httpx.Response:
    if request.method == "POST" and request.url.path.endswith("/cancel"):
        return httpx.Response(200, json={"run_id": "run_abc", "status": "cancelled"})
    return _refuse_runner(request)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(ENV_PREFIXES):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DASHBOARD_USER", USERNAME)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", _PASSWORD_HASH)
    monkeypatch.setenv("DASHBOARD_TOKEN", TOKEN)
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", SESSION_SECRET)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _refuse_network)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse_network)


@pytest.fixture
def app() -> FastAPI:
    return create_dashboard_app(DashboardSettings(), transport=httpx.MockTransport(_refuse_runner))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as test_client:
        yield test_client


def _cookie_header(value: str) -> dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE}={value}"}


def _bearer_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _session_value(response: httpx.Response) -> str:
    return response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]


def _login(client: TestClient) -> str:
    response = client.post("/login", data={"username": USERNAME, "password": PASSWORD})
    assert response.status_code == 303, response.text
    value = _session_value(response)
    client.cookies.clear()
    return value


def _route_pairs(app: FastAPI) -> list[tuple[str, str]]:
    """(method, path) of every HTTP route, descending into the included board router."""
    pairs: list[tuple[str, str]] = []
    for entry in app.routes:
        included = getattr(entry, "original_router", None)
        routes = included.routes if included is not None else [entry]
        for route in routes:
            if not isinstance(route, (APIRoute, Route)):
                raise AssertionError(f"route type not covered by the inventory: {route!r}")
            pairs.extend((method, route.path) for method in sorted(route.methods or ()))
    return pairs


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", PLACEHOLDER, path)


def test_unauthenticated_html_request_redirects_to_login(client: TestClient) -> None:
    response = client.get("/", headers={"Accept": "text/html"})
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_unauthenticated_api_request_gets_401_json(client: TestClient) -> None:
    response = client.get("/api/guards", headers=JSON_ACCEPT)
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


def test_login_sets_hardened_session_cookie(client: TestClient) -> None:
    response = client.post("/login", data={"username": USERNAME, "password": PASSWORD})
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie


def test_wrong_password_is_refused_without_session(client: TestClient) -> None:
    response = client.post("/login", data={"username": USERNAME, "password": "wrong-guess"})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert PASSWORD not in response.text


def test_login_failures_are_rate_limited(client: TestClient) -> None:
    for _ in range(6):
        response = client.post("/login", data={"username": USERNAME, "password": "wrong"})
        assert response.status_code == 401
    locked = client.post("/login", data={"username": USERNAME, "password": PASSWORD})
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0


def test_session_cookie_from_login_grants_api_access(client: TestClient) -> None:
    value = _login(client)
    response = client.get("/api/guards", headers=_cookie_header(value) | JSON_ACCEPT)
    assert response.status_code == 200


def test_tampered_session_cookie_is_refused(client: TestClient) -> None:
    value = _login(client)
    flipped = value[:-1] + ("A" if value[-1] != "A" else "B")
    response = client.get("/api/guards", headers=_cookie_header(flipped) | JSON_ACCEPT)
    assert response.status_code == 401


def test_session_signed_with_another_secret_is_refused(client: TestClient) -> None:
    forged = issue_session("another-secret", USERNAME, int(time.time()), 3600)
    response = client.get("/api/guards", headers=_cookie_header(forged) | JSON_ACCEPT)
    assert response.status_code == 401


def test_expired_session_cookie_is_refused(client: TestClient) -> None:
    expired = issue_session(SESSION_SECRET, USERNAME, int(time.time()) - 7200, 60)
    response = client.get("/api/guards", headers=_cookie_header(expired) | JSON_ACCEPT)
    assert response.status_code == 401


def test_bearer_token_grants_api_access(client: TestClient) -> None:
    response = client.get("/api/guards", headers=_bearer_header(TOKEN) | JSON_ACCEPT)
    assert response.status_code == 200


def test_wrong_bearer_token_is_refused(client: TestClient) -> None:
    response = client.get("/api/guards", headers=_bearer_header("not-the-token") | JSON_ACCEPT)
    assert response.status_code == 401


def test_logout_clears_the_session_cookie(client: TestClient) -> None:
    response = client.get("/logout")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "Max-Age=0" in cookie


def test_foreign_host_header_is_rejected_even_with_valid_bearer(client: TestClient) -> None:
    headers = {"Host": "evil.example"} | _bearer_header(TOKEN)
    response = client.get("/api/guards", headers=headers | JSON_ACCEPT)
    assert response.status_code == 400


def test_extra_host_from_env_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_HOSTS_EXTRA", "lan.example")
    app = create_dashboard_app(DashboardSettings(), transport=httpx.MockTransport(_refuse_runner))
    with TestClient(app, base_url=BASE_URL) as client:
        response = client.get("/healthz", headers={"Host": "lan.example"})
    assert response.status_code == 200


def test_dashboard_header_is_required_on_every_mutating_route(app: FastAPI) -> None:
    refused: list[str] = []
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
        value = _login(client)
        for method, path in _route_pairs(app):
            if method not in MUTATING_METHODS or (method, path) in PUBLIC_ROUTES:
                continue
            response = client.request(
                method, _concrete(path), headers=_cookie_header(value) | JSON_ACCEPT, json={}
            )
            if response.status_code != 403:
                refused.append(f"{method} {path} -> {response.status_code}")
    assert not refused, refused


def test_authenticated_mutation_with_dashboard_header_passes_the_guard(client: TestClient) -> None:
    value = _login(client)
    response = client.post(
        "/api/board/queue/first",
        headers=_cookie_header(value) | DASHBOARD_HEADER | JSON_ACCEPT,
        json={},
    )
    assert response.status_code not in (401, 403)


def test_public_routes_answer_as_allowlisted(client: TestClient) -> None:
    mismatched: dict[tuple[str, str], int] = {}
    for (method, path), expected in PUBLIC_ROUTES.items():
        status = client.request(method, path).status_code
        if status != expected:
            mismatched[(method, path)] = status
    assert not mismatched, mismatched


def test_every_route_is_public_or_refuses_unauthenticated_requests(app: FastAPI) -> None:
    unprotected: list[str] = []
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
        for method, path in _route_pairs(app):
            if (method, path) in PUBLIC_ROUTES:
                continue
            response = client.request(
                method, _concrete(path), headers=DASHBOARD_HEADER | JSON_ACCEPT
            )
            if response.status_code != 401:
                unprotected.append(f"{method} {path} -> {response.status_code}")
    assert not unprotected, unprotected


def test_public_allowlist_has_no_stale_entries(app: FastAPI) -> None:
    assert set(PUBLIC_ROUTES) <= set(_route_pairs(app))


def test_caplog_has_no_password_or_token(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client.post("/login", data={"username": USERNAME, "password": "wrong-guess"})
    client.post("/login", data={"username": USERNAME, "password": PASSWORD})
    client.post("/login", data={"token": "wrong"})
    client.get("/api/guards", headers=_bearer_header(TOKEN))
    client.get("/api/guards", headers=_bearer_header("wrong"))
    assert PASSWORD not in caplog.text
    assert TOKEN not in caplog.text


def test_response_bodies_have_no_token_or_password_hash(client: TestClient) -> None:
    bodies = [
        client.post("/login", data={"username": USERNAME, "password": "wrong-guess"}).text,
        client.post("/login", data={"username": USERNAME, "password": PASSWORD}).text,
        client.post("/login", data={"token": "wrong"}).text,
        client.get("/api/guards", headers=_bearer_header(TOKEN)).text,
        client.get("/api/guards", headers=_bearer_header("wrong")).text,
        client.get("/healthz").text,
    ]
    for body in bodies:
        assert TOKEN not in body
        assert _PASSWORD_HASH not in body


def test_html_routes_redirect_to_login_without_session(app: FastAPI) -> None:
    unredirected: list[str] = []
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
        for method, path in _route_pairs(app):
            if method != "GET" or (method, path) in PUBLIC_ROUTES:
                continue
            response = client.get(_concrete(path), headers={"Accept": "text/html"})
            if response.status_code != 303 or response.headers.get("location") != "/login":
                unredirected.append(f"{method} {path} -> {response.status_code}")
    assert not unredirected, unredirected


def test_healthz_answers_without_run_data(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "sources"}
    assert body["status"] == "ok"


def test_stop_without_session_is_refused_even_with_dashboard_header(client: TestClient) -> None:
    response = client.post("/api/runs/run_abc/cancel", headers=DASHBOARD_HEADER | JSON_ACCEPT)
    assert response.status_code == 401


def test_stop_with_session_but_without_dashboard_header_is_forbidden(client: TestClient) -> None:
    value = _login(client)
    response = client.post("/api/runs/run_abc/cancel", headers=_cookie_header(value) | JSON_ACCEPT)
    assert response.status_code == 403


def test_stop_with_session_and_dashboard_header_reaches_the_runner() -> None:
    app = create_dashboard_app(
        DashboardSettings(), transport=httpx.MockTransport(_runner_answers_cancel)
    )
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
        value = _login(client)
        response = client.post(
            "/api/runs/run_abc/cancel",
            headers=_cookie_header(value) | DASHBOARD_HEADER | JSON_ACCEPT,
        )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_stop_is_forbidden_in_readonly_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_READONLY", "1")
    app = create_dashboard_app(DashboardSettings(), transport=httpx.MockTransport(_refuse_runner))
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
        value = _login(client)
        response = client.post(
            "/api/runs/run_abc/cancel",
            headers=_cookie_header(value) | DASHBOARD_HEADER | JSON_ACCEPT,
        )
    assert response.status_code == 403
    assert "read-only" in response.json()["error"]


def test_readonly_mode_refuses_every_write_and_tells_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = DASHBOARD_HEADER | JSON_ACCEPT
    monkeypatch.setenv("DASHBOARD_READONLY", "1")
    app = create_dashboard_app(DashboardSettings(), transport=httpx.MockTransport(_refuse_runner))
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
        cookie = _cookie_header(_login(client))
        assert client.get("/api/ui-mode", headers=cookie).json() == {"readonly": True}
        for method, path in (
            ("POST", "/api/board/missions/create"),
            ("PUT", "/api/board/queue"),
            ("DELETE", "/api/board/missions/x"),
            ("POST", "/api/runs/run_abc/cancel"),
        ):
            response = client.request(method, path, headers=cookie | headers)
            assert response.status_code == 403, (method, path)
            assert "read-only" in response.json()["error"]


def test_writes_are_not_blocked_when_readonly_is_off() -> None:
    app = create_dashboard_app(DashboardSettings(), transport=httpx.MockTransport(_refuse_runner))
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
        cookie = _cookie_header(_login(client))
        assert client.get("/api/ui-mode", headers=cookie).json() == {"readonly": False}
        response = client.post(
            "/api/board/missions/create", headers=cookie | DASHBOARD_HEADER, json={}
        )
        assert response.status_code != 403


def test_board_save_without_dashboard_header_is_refused_with_403() -> None:
    save = {"expected_version": "2026-01-01T00:00:00+00:00", "overrides_json": "{}"}
    path = "/api/board/missions/m1"
    app = create_dashboard_app(DashboardSettings(), transport=httpx.MockTransport(_refuse_runner))
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
        value = _login(client)
        refused = client.post(path, headers=_cookie_header(value) | JSON_ACCEPT, json=save)
        allowed = client.post(
            path, headers=_cookie_header(value) | DASHBOARD_HEADER | JSON_ACCEPT, json=save
        )
    assert refused.status_code == 403
    assert refused.json() == {"error": "missing X-AHAWR-Dashboard header"}
    assert allowed.status_code != 403
