from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from claude_runner.dashboard import create_dashboard_app

from .test_dashboard_host_actions import HEADER, OPERATOR_KEY, REQUEST_ID, settings, stub_runner


@pytest.mark.parametrize("path", ["confirm", "reject"])
def test_flag_off_status_is_disabled_and_writes_change_nothing(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.delenv("HOST_ACTIONS", raising=False)
    calls = stub_runner(monkeypatch, {"tier": "CONFIRM", "state": "pending"})
    with TestClient(create_dashboard_app(settings()), base_url="http://localhost") as client:
        assert client.get("/api/host-actions").json() == {"enabled": False}
        response = client.post(f"/api/host-actions/{REQUEST_ID}/{path}", headers=HEADER)
    assert response.status_code == 404
    assert calls == []


@pytest.mark.parametrize("path", ["confirm", "reject"])
def test_write_without_header_is_403_and_calls_nothing(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setenv("HOST_ACTIONS", "1")
    calls = stub_runner(monkeypatch, {"tier": "CONFIRM", "state": "pending"})
    with TestClient(create_dashboard_app(settings()), base_url="http://localhost") as client:
        response = client.post(f"/api/host-actions/{REQUEST_ID}/{path}")
    assert response.status_code == 403
    assert calls == []


def test_confirm_pending_confirm_tier_with_header_is_approved(
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


@pytest.mark.parametrize(
    ("tier", "state"),
    [
        ("AUTO", "pending"),
        ("CONFIRM", "confirmed"),
        ("CONFIRM", "running"),
        ("CONFIRM", "done"),
        ("CONFIRM", "rejected"),
        ("CONFIRM", "expired"),
    ],
)
def test_confirm_of_auto_or_non_pending_request_is_refused(
    monkeypatch: pytest.MonkeyPatch, tier: str, state: str
) -> None:
    monkeypatch.setenv("HOST_ACTIONS", "1")
    calls = stub_runner(monkeypatch, {"tier": tier, "state": state})
    with TestClient(create_dashboard_app(settings()), base_url="http://localhost") as client:
        response = client.post(f"/api/host-actions/{REQUEST_ID}/confirm", headers=HEADER)
    assert response.status_code == 409
    assert all(method == "GET" for method, *_ in calls)
