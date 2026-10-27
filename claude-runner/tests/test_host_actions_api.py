"""Tests for the /v1/host-actions routes: the flag off, the flag on, the whitelist refusals, extra
body fields, and the operator route that decides confirm and reject for the dashboard."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields, replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings
from claude_runner.guards import Notifier
from claude_runner.host_actions import HostActionRequest

TOKEN = "test-host-token"
OPERATOR_KEY = "test-operator-key"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
OPERATOR_AUTH = {"Authorization": f"Bearer {OPERATOR_KEY}"}
STORE_FILE = "host_actions.json"
ROUTES = (
    ("POST", "/v1/host-actions", {"action": "restart", "service": "ahawr-qa", "reason": "r"}),
    ("GET", "/v1/host-actions", None),
    ("GET", "/v1/host-actions/abc", None),
    ("POST", "/v1/host-actions/claim", None),
    ("POST", "/v1/host-actions/abc/result", {"ok": True, "message": "x"}),
    ("POST", "/v1/host-actions/abc/operator", {"approved": True}),
)


@pytest.fixture
def host_off(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("HOST_ACTIONS", raising=False)
    monkeypatch.setenv("CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY", OPERATOR_KEY)
    s = replace(settings, api_key=TOKEN, host_actions_operator_key=OPERATOR_KEY)
    with TestClient(create_app(s)) as c:
        yield c


@pytest.fixture
def host_on(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("HOST_ACTIONS", "1")
    monkeypatch.setenv("CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY", OPERATOR_KEY)
    s = replace(settings, api_key=TOKEN, host_actions_operator_key=OPERATOR_KEY)
    with TestClient(create_app(s)) as c:
        yield c


def _create(client: TestClient, **overrides: str) -> dict[str, Any]:
    body = {"action": "restart", "service": "ahawr-qa", "reason": "restart the QA container"}
    response = client.post("/v1/host-actions", json={**body, **overrides}, headers=AUTH)
    assert response.status_code == 200
    return response.json()


@pytest.fixture(autouse=True)
def notices(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    async def fake_send(self: Notifier, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(Notifier, "send", fake_send)
    return sent


def test_confirm_tier_create_sends_one_notice_with_id_service_action_and_reason(
    host_on: TestClient, notices: list[str]
) -> None:
    created = _create(host_on, service="claude-runner", reason="rebuild the runner")
    assert created["tier"] == "CONFIRM"
    assert len(notices) == 1
    assert created["id"] in notices[0]
    assert "claude-runner" in notices[0]
    assert "restart" in notices[0]
    assert "rebuild the runner" in notices[0]


def test_auto_tier_create_sends_no_notice(host_on: TestClient, notices: list[str]) -> None:
    assert _create(host_on)["tier"] == "AUTO"
    assert notices == []


def test_flag_off_sends_no_notice(host_off: TestClient, notices: list[str]) -> None:
    response = host_off.post(
        "/v1/host-actions",
        json={"action": "restart", "service": "claude-runner", "reason": "r"},
        headers=AUTH,
    )
    assert response.status_code == 404
    assert notices == []


def test_failed_notice_is_logged_and_does_not_fail_the_request(
    host_on: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def failing_send(self: Notifier, text: str) -> bool:
        raise RuntimeError("telegram unreachable")

    monkeypatch.setattr(Notifier, "send", failing_send)
    with caplog.at_level("ERROR", logger="claude_runner.api"):
        created = _create(host_on, service="claude-runner")
    assert created["tier"] == "CONFIRM"
    assert "confirm notice failed" in caplog.text
    assert "telegram unreachable" in caplog.text


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_flag_off_answers_404_and_writes_no_store_file(
    host_off: TestClient, settings: Settings, method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = host_off.request(method, path, json=body, headers=AUTH)
    assert response.status_code == 404
    assert not (settings.data_dir / STORE_FILE).exists()


def test_create_list_and_get_work_with_the_flag_on(host_on: TestClient, settings: Settings) -> None:
    created = _create(host_on)
    assert created["state"] == "pending"
    assert created["tier"] == "AUTO"
    assert created["requested_by"] == "worker"
    listed = host_on.get("/v1/host-actions?status=pending", headers=AUTH)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [created["id"]]
    found = host_on.get(f"/v1/host-actions/{created['id']}", headers=AUTH)
    assert found.status_code == 200
    assert found.json()["id"] == created["id"]
    assert (settings.data_dir / STORE_FILE).exists()


def test_claim_then_result_finishes_a_confirmed_request(host_on: TestClient) -> None:
    created = _create(host_on)
    refused = host_on.post("/v1/host-actions/claim", headers=AUTH)
    assert refused.status_code == 409
    assert refused.json()["code"] == "host_action_none_ready"
    host_on.app.state.host_actions.confirm(created["id"])
    claimed = host_on.post("/v1/host-actions/claim", headers=AUTH)
    assert claimed.status_code == 200
    assert claimed.json()["state"] == "running"
    finished = host_on.post(
        f"/v1/host-actions/{created['id']}/result",
        json={"ok": True, "message": "restarted"},
        headers=AUTH,
    )
    assert finished.status_code == 200
    assert finished.json()["state"] == "done"
    assert finished.json()["message"] == "restarted"


@pytest.mark.parametrize(
    ("action", "service", "message"),
    [
        ("delete", "ahawr-qa", "unknown action 'delete'"),
        ("restart", "postgres", "unknown service 'postgres'"),
        ("restart", "ahawr-postgres", "service 'ahawr-postgres' is not whitelisted"),
    ],
)
def test_unknown_or_not_whitelisted_request_is_refused_with_400(
    host_on: TestClient, action: str, service: str, message: str
) -> None:
    response = host_on.post(
        "/v1/host-actions",
        json={"action": action, "service": service, "reason": "r"},
        headers=AUTH,
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == message


def test_confirm_and_reject_are_absent_on_the_runner_port(host_on: TestClient) -> None:
    created = _create(host_on)
    for decision in ("confirm", "reject"):
        response = host_on.post(
            f"/v1/host-actions/{created['id']}/{decision}", json={"approved": True}, headers=AUTH
        )
        assert response.status_code in (404, 405)
    after = host_on.get(f"/v1/host-actions/{created['id']}", headers=AUTH)
    assert after.json()["state"] == "pending"


def test_operator_route_confirms_a_confirm_tier_request(host_on: TestClient) -> None:
    created = _create(host_on, service="claude-runner")
    assert created["tier"] == "CONFIRM"
    response = host_on.post(
        f"/v1/host-actions/{created['id']}/operator", json={"approved": True}, headers=OPERATOR_AUTH
    )
    assert response.status_code == 200
    assert response.json()["state"] == "confirmed"


def test_operator_route_rejects_pending_and_confirmed(host_on: TestClient) -> None:
    first = _create(host_on)
    refused = host_on.post(
        f"/v1/host-actions/{first['id']}/operator", json={"approved": False}, headers=OPERATOR_AUTH
    )
    assert refused.status_code == 200
    assert refused.json()["state"] == "rejected"
    second = _create(host_on)
    host_on.app.state.host_actions.confirm(second["id"])
    refused = host_on.post(
        f"/v1/host-actions/{second['id']}/operator", json={"approved": False}, headers=OPERATOR_AUTH
    )
    assert refused.status_code == 200
    assert refused.json()["state"] == "rejected"


def test_operator_route_refuses_non_pending_approve_and_wrong_token(
    host_on: TestClient,
) -> None:
    created = _create(host_on)
    refused = host_on.post(
        f"/v1/host-actions/{created['id']}/operator", json={"approved": True}, headers=AUTH
    )
    assert refused.status_code == 401
    after = host_on.get(f"/v1/host-actions/{created['id']}", headers=AUTH)
    assert after.json()["state"] == "pending"
    host_on.app.state.host_actions.confirm(created["id"])
    refused = host_on.post(
        f"/v1/host-actions/{created['id']}/operator", json={"approved": True}, headers=OPERATOR_AUTH
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "host_action_transition_refused"


def test_extra_body_fields_are_never_stored_or_returned(
    host_on: TestClient, settings: Settings
) -> None:
    body = {
        "action": "restart",
        "service": "ahawr-qa",
        "reason": "r",
        "args": ["--force"],
        "command": "rm -rf /",
    }
    response = host_on.post("/v1/host-actions", json=body, headers=AUTH)
    assert response.status_code == 200
    assert set(response.json()) == {f.name for f in fields(HostActionRequest)}
    stored = (settings.data_dir / STORE_FILE).read_text(encoding="utf-8")
    for leaked in ("--force", "rm -rf", '"args"', '"command"'):
        assert leaked not in stored
