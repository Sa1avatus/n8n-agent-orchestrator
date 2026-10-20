"""Tests for the /api/guards route: the default-off behaviour, the enabled
state with a fake guard, and the guarantee that no environment value (a
made-up Telegram token here) ever appears in the response.

The dashboard app is built with the existing mock-transport fixture; no
network is used.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from claude_runner.guards import Finding, Guard

from .test_dashboard import dashboard


class FiringGuard(Guard):
    """A guard that succeeds, reports one finding, and performs one action."""

    def __init__(self, name: str = "firing", interval: float = 0.01) -> None:
        super().__init__(name, interval)
        self.act_calls = 0

    async def check(self) -> list[Finding]:
        return [Finding("one finding")]

    async def act(self, findings: list[Finding]) -> str | None:
        self.act_calls += 1
        return f"acted on {len(findings)}"


def test_guards_route_off_by_default(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """With DASHBOARD_GUARDS unset the route reports disabled and no supervisor
    tasks exist; no per-guard task is created and the supervisor stops at
    shutdown."""
    monkeypatch.delenv("DASHBOARD_GUARDS", raising=False)
    with dashboard(client) as dash:
        body = dash.get("/api/guards").json()
        assert body["enabled"] is False
        assert body["guards"] == []
        # no per-guard task is created while the master is off
        assert dash.app.state.guards._tasks == {}
        # the supervisor is running during the request
        assert dash.app.state.guards._running is True
    # the shutdown path stops the supervisor
    assert dash.app.state.guards._running is False


def test_guards_route_with_fake_guards(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """With DASHBOARD_GUARDS=1 and a registered fake guard the state is
    returned with the expected fields: name, status, last_run, last_result,
    last_action and error_count."""
    monkeypatch.setenv("DASHBOARD_GUARDS", "1")
    guard = FiringGuard("firing", interval=0.01)
    with dashboard(client) as dash:
        # register the fake guard on the running supervisor so the route
        # reports it with all its state fields
        dash.app.state.guards.guards.append(guard)
        body = dash.get("/api/guards").json()
        assert body["enabled"] is True
        assert body["running"] is True
        assert len(body["guards"]) == 1
        g = body["guards"][0]
        assert g["name"] == "firing"
        for key in ("status", "last_run", "last_result", "last_action", "error_count"):
            assert key in g
        # initial state before any check has run
        assert g["status"] == "ok"
        assert g["last_run"] == 0.0
        assert g["last_result"] is None
        assert g["last_action"] is None
        assert g["error_count"] == 0
    assert dash.app.state.guards._running is False


def test_guards_route_no_secret(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A made-up token and chat id set in the environment never appear in the
    response, even with guards enabled."""
    token = "fake-token-12345"
    chat = "987654321"
    monkeypatch.setenv("DASHBOARD_GUARDS", "1")
    monkeypatch.setenv("DASHBOARD_TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("DASHBOARD_TELEGRAM_CHAT_ID", chat)
    with dashboard(client) as dash:
        body = dash.get("/api/guards").json()
        text = body.__repr__()
        assert token not in text
        assert chat not in text
    assert dash.app.state.guards._running is False
