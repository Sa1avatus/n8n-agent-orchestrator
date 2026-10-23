"""Tests for the /v1/qa/requests routes: the bearer check on claim and result, body
validation at the boundary, 404 for an unknown id and 409 for an illegal transition."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings

TOKEN = "test-qa-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def guarded(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(replace(settings, api_key=TOKEN))) as c:
        yield c


def _create(client: TestClient, nonce: str = "n1") -> str:
    body = {"mission_id": "m1", "working_directory": "/w", "mode": "ui_tasks", "nonce": nonce}
    response = client.post("/v1/qa/requests", json=body, headers=AUTH)
    assert response.status_code == 200
    return str(response.json()["id"])


def test_create_is_idempotent_by_mission_and_nonce(guarded: TestClient) -> None:
    first = _create(guarded)
    assert _create(guarded) == first


def test_claim_and_result_refuse_a_missing_bearer(guarded: TestClient) -> None:
    request_id = _create(guarded)
    assert guarded.post(f"/v1/qa/requests/{request_id}/claim").status_code == 401
    result = guarded.post(f"/v1/qa/requests/{request_id}/result", json={"verdicts": ["pass"]})
    assert result.status_code == 401


def test_bad_mode_and_bad_verdicts_are_rejected(guarded: TestClient) -> None:
    bad_mode = {"mission_id": "m1", "mode": "bogus", "nonce": "n2"}
    assert guarded.post("/v1/qa/requests", json=bad_mode, headers=AUTH).status_code == 422
    request_id = _create(guarded)
    guarded.post(f"/v1/qa/requests/{request_id}/claim", headers=AUTH)
    for verdicts in (["maybe"], "pass"):
        response = guarded.post(
            f"/v1/qa/requests/{request_id}/result", json={"verdicts": verdicts}, headers=AUTH
        )
        assert response.status_code == 422


def test_claim_then_result_moves_the_request_to_done(guarded: TestClient) -> None:
    request_id = _create(guarded)
    claimed = guarded.post(f"/v1/qa/requests/{request_id}/claim", headers=AUTH)
    assert claimed.json()["status"] == "running"
    finished = guarded.post(
        f"/v1/qa/requests/{request_id}/result",
        json={"verdicts": ["pass", "skipped"]},
        headers=AUTH,
    )
    assert finished.json()["status"] == "done"
    assert guarded.get(f"/v1/qa/requests/{request_id}", headers=AUTH).json()["verdicts"] == [
        "pass",
        "skipped",
    ]


def test_result_before_claim_is_409_and_unknown_id_is_404(guarded: TestClient) -> None:
    request_id = _create(guarded)
    early = guarded.post(
        f"/v1/qa/requests/{request_id}/result", json={"verdicts": ["pass"]}, headers=AUTH
    )
    assert early.status_code == 409
    assert guarded.get("/v1/qa/requests/nope", headers=AUTH).status_code == 404
    assert guarded.post("/v1/qa/requests/nope/claim", headers=AUTH).status_code == 404


def test_list_filters_by_status_and_rejects_unknown_status(guarded: TestClient) -> None:
    _create(guarded, nonce="queued-one")
    running_id = _create(guarded, nonce="running-one")
    guarded.post(f"/v1/qa/requests/{running_id}/claim", headers=AUTH)
    queued = guarded.get("/v1/qa/requests", params={"status": "queued"}, headers=AUTH).json()
    assert [item["nonce"] for item in queued] == ["queued-one"]
    unknown = guarded.get("/v1/qa/requests", params={"status": "bogus"}, headers=AUTH)
    assert unknown.status_code == 422
