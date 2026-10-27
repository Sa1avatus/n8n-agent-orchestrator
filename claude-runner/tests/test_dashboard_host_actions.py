from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from claude_runner import dashboard as dashboard_module
from claude_runner.dashboard import DashboardSettings, create_dashboard_app

REQUEST_ID = "a" * 32
HEADER = {"X-AHAWR-Dashboard": "1"}
OPERATOR_KEY = "operator-test-key"


def stub_runner(
    monkeypatch: pytest.MonkeyPatch, record: dict[str, Any] | None = None
) -> list[tuple[str, str, str, Any]]:
    calls: list[tuple[str, str, str, Any]] = []

    async def runner(
        settings: DashboardSettings,
        method: str,
        path: str,
        key: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        calls.append((method, path, key, body))
        return 200, record or {}

    monkeypatch.setattr(dashboard_module, "runner_host_action", runner)
    return calls


def settings() -> DashboardSettings:
    return DashboardSettings(host_actions_operator_key=OPERATOR_KEY)


def test_flag_off_get_reports_disabled_and_calls_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HOST_ACTIONS", raising=False)
    calls = stub_runner(monkeypatch)
    with TestClient(create_dashboard_app(settings()), base_url="http://localhost") as client:
        assert client.get("/api/host-actions").json() == {"enabled": False}
    assert calls == []


@pytest.mark.parametrize("path", ["confirm", "reject"])
def test_flag_off_write_is_404_and_calls_nothing(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.delenv("HOST_ACTIONS", raising=False)
    calls = stub_runner(monkeypatch)
    with TestClient(create_dashboard_app(settings()), base_url="http://localhost") as client:
        response = client.post(f"/api/host-actions/{REQUEST_ID}/{path}", headers=HEADER)
    assert response.status_code == 404
    assert calls == []


@pytest.mark.parametrize("path", ["confirm", "reject"])
def test_write_without_header_is_403(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setenv("HOST_ACTIONS", "1")
    calls = stub_runner(monkeypatch, {"tier": "CONFIRM", "state": "pending"})
    with TestClient(create_dashboard_app(settings()), base_url="http://localhost") as client:
        response = client.post(f"/api/host-actions/{REQUEST_ID}/{path}")
    assert response.status_code == 403
    assert calls == []


def test_confirm_pending_confirm_tier_sends_approval_with_operator_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_ACTIONS", "1")
    calls = stub_runner(monkeypatch, {"tier": "CONFIRM", "state": "pending"})
    with TestClient(create_dashboard_app(settings()), base_url="http://localhost") as client:
        response = client.post(f"/api/host-actions/{REQUEST_ID}/confirm", headers=HEADER)
    assert response.status_code == 200
    assert calls[-1] == (
        "POST",
        f"/v1/host-actions/{REQUEST_ID}/operator",
        OPERATOR_KEY,
        {"approved": True},
    )


def test_confirm_auto_tier_is_refused_without_a_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_ACTIONS", "1")
    calls = stub_runner(monkeypatch, {"tier": "AUTO", "state": "pending"})
    with TestClient(create_dashboard_app(settings()), base_url="http://localhost") as client:
        response = client.post(f"/api/host-actions/{REQUEST_ID}/confirm", headers=HEADER)
    assert response.status_code == 409
    assert all(method == "GET" for method, *_ in calls)


def test_reject_sends_refusal_with_operator_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_ACTIONS", "1")
    calls = stub_runner(monkeypatch)
    with TestClient(create_dashboard_app(settings()), base_url="http://localhost") as client:
        response = client.post(f"/api/host-actions/{REQUEST_ID}/reject", headers=HEADER)
    assert response.status_code == 200
    assert calls == [
        (
            "POST",
            f"/v1/host-actions/{REQUEST_ID}/operator",
            OPERATOR_KEY,
            {"approved": False},
        )
    ]


def test_malformed_id_is_404_and_calls_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_ACTIONS", "1")
    calls = stub_runner(monkeypatch)
    with TestClient(create_dashboard_app(settings()), base_url="http://localhost") as client:
        response = client.post("/api/host-actions/not-an-id/reject", headers=HEADER)
    assert response.status_code == 404
    assert calls == []
