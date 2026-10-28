"""API tests for the /v1/qa/requests routes: the create, claim and result flow, the bearer
check, the 404 and 409 refusals, expiry seen through GET, and no secret in any response."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings
from claude_runner.qa_requests import QA_CLAIM_WAIT_MINUTES, QA_RUN_BOUND_MINUTES, QaRequestStore

SECRET = "qa-config-secret-7f3a9c"
WRONG_BEARER = "qa-wrong-bearer-41b2e8"
AUTH = {"Authorization": f"Bearer {SECRET}"}
WRONG_AUTH = {"Authorization": f"Bearer {WRONG_BEARER}"}
START = datetime(2026, 1, 1, tzinfo=UTC)
ROUTE_PREFIX = "/v1/qa"
REQUESTS = "/v1/qa/requests"
ONE = "/v1/qa/requests/{request_id}"
CLAIM = "/v1/qa/requests/{request_id}/claim"
RESULT = "/v1/qa/requests/{request_id}/result"


class FakeClock:
    def __init__(self) -> None:
        self.at = START

    def __call__(self) -> str:
        return self.at.isoformat(timespec="seconds")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def qa(settings: Settings, clock: FakeClock) -> Iterator[TestClient]:
    with TestClient(create_app(replace(settings, api_key=SECRET))) as client:
        cast(FastAPI, client.app).state.qa_requests = QaRequestStore(
            str(settings.data_dir / "qa_requests.json"), clock=clock
        )
        yield client


def _create_body(
    nonce: str = "n1",
    mission_id: str = "m1",
    layers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "mission_id": mission_id,
        "working_directory": "/w",
        "mode": "ui_tasks",
        "nonce": nonce,
        "layers": layers,
    }


def _create(client: TestClient, nonce: str = "n1") -> str:
    response = client.post(REQUESTS, json=_create_body(nonce), headers=AUTH)
    assert response.status_code == 200
    return str(response.json()["id"])


def _list(client: TestClient, status: str | None = None) -> list[str]:
    params = {"status": status} if status else {}
    response = client.get(REQUESTS, params=params, headers=AUTH)
    assert response.status_code == 200
    return [str(item["id"]) for item in response.json()]


def test_create_get_list_claim_result_flow(qa: TestClient) -> None:
    request_id = _create(qa)
    assert qa.get(ONE.format(request_id=request_id), headers=AUTH).json()["status"] == "queued"
    assert _list(qa, "queued") == [request_id]

    claimed = qa.post(CLAIM.format(request_id=request_id), headers=AUTH)
    assert claimed.status_code == 200
    assert claimed.json()["status"] == "running"
    assert _list(qa, "running") == [request_id]

    finished = qa.post(
        RESULT.format(request_id=request_id), json={"verdicts": ["pass", "fail"]}, headers=AUTH
    )
    assert finished.status_code == 200
    body = qa.get(ONE.format(request_id=request_id), headers=AUTH).json()
    assert body["status"] == "done"
    assert body["verdicts"] == ["pass", "fail"]
    assert _list(qa, "done") == [request_id]
    assert _list(qa) == [request_id]


def test_create_is_idempotent_by_mission_and_nonce(qa: TestClient) -> None:
    first = _create(qa)
    assert _create(qa) == first
    assert _list(qa) == [first]
    assert _create(qa, nonce="n2") != first


@pytest.mark.parametrize("headers", [{}, WRONG_AUTH], ids=["no-bearer", "wrong-bearer"])
def test_claim_and_result_refuse_missing_or_wrong_bearer(
    qa: TestClient, headers: dict[str, str]
) -> None:
    request_id = _create(qa)
    claim = qa.post(CLAIM.format(request_id=request_id), headers=headers)
    assert claim.status_code == 401
    result = qa.post(
        RESULT.format(request_id=request_id), json={"verdicts": ["pass"]}, headers=headers
    )
    assert result.status_code == 401
    assert qa.get(ONE.format(request_id=request_id), headers=AUTH).json()["status"] == "queued"


def test_illegal_transition_is_409_and_leaves_the_state(qa: TestClient) -> None:
    request_id = _create(qa)
    early = qa.post(
        RESULT.format(request_id=request_id), json={"verdicts": ["pass"]}, headers=AUTH
    )
    assert early.status_code == 409
    assert early.json()["code"] == "qa_transition_refused"

    assert qa.post(CLAIM.format(request_id=request_id), headers=AUTH).status_code == 200
    assert qa.post(CLAIM.format(request_id=request_id), headers=AUTH).status_code == 409
    assert (
        qa.post(
            RESULT.format(request_id=request_id), json={"verdicts": ["pass"]}, headers=AUTH
        ).status_code
        == 200
    )
    twice = qa.post(
        RESULT.format(request_id=request_id), json={"verdicts": ["pass"]}, headers=AUTH
    )
    assert twice.status_code == 409
    assert qa.get(ONE.format(request_id=request_id), headers=AUTH).json()["status"] == "done"


def test_unknown_id_is_404_on_get_claim_and_result(qa: TestClient) -> None:
    get = qa.get(ONE.format(request_id="nope"), headers=AUTH)
    assert get.status_code == 404
    assert get.json()["code"] == "qa_request_not_found"
    assert qa.post(CLAIM.format(request_id="nope"), headers=AUTH).status_code == 404
    result = qa.post(RESULT.format(request_id="nope"), json={"verdicts": []}, headers=AUTH)
    assert result.status_code == 404


def test_queued_request_past_the_claim_wait_is_timeout_through_get(
    qa: TestClient, clock: FakeClock
) -> None:
    request_id = _create(qa)
    clock.at = START + timedelta(minutes=QA_CLAIM_WAIT_MINUTES + 1)
    assert qa.get(ONE.format(request_id=request_id), headers=AUTH).json()["status"] == "timeout"
    assert _list(qa, "timeout") == [request_id]
    late = qa.post(CLAIM.format(request_id=request_id), headers=AUTH)
    assert late.status_code == 409


def test_running_request_past_the_run_bound_is_timeout_through_get(
    qa: TestClient, clock: FakeClock
) -> None:
    request_id = _create(qa)
    assert qa.post(CLAIM.format(request_id=request_id), headers=AUTH).status_code == 200
    clock.at = START + timedelta(minutes=QA_RUN_BOUND_MINUTES + 1)
    assert qa.get(ONE.format(request_id=request_id), headers=AUTH).json()["status"] == "timeout"


def test_post_without_layers_returns_no_layers_key(qa: TestClient) -> None:
    response = qa.post(REQUESTS, json=_create_body(), headers=AUTH)
    assert response.status_code == 200
    assert "layers" not in response.json()


def test_post_with_valid_layers_returns_them(qa: TestClient) -> None:
    response = qa.post(
        REQUESTS, json=_create_body(nonce="n2", layers=["regression", "ui"]), headers=AUTH
    )
    assert response.status_code == 200
    assert response.json()["layers"] == ["regression", "ui"]
    body = qa.get(ONE.format(request_id=response.json()["id"]), headers=AUTH).json()
    assert body["layers"] == ["regression", "ui"]
    listed = qa.get(REQUESTS, headers=AUTH).json()
    for item in listed:
        if item["id"] == body["id"]:
            assert item["layers"] == ["regression", "ui"]


def test_post_with_unknown_layer_is_refused(qa: TestClient) -> None:
    response = qa.post(
        REQUESTS, json=_create_body(nonce="n3", layers=["bogus"]), headers=AUTH
    )
    assert 400 <= response.status_code < 500


def test_claim_and_result_of_request_without_layers_have_no_layers_key(qa: TestClient) -> None:
    request_id = _create(qa)
    claimed = qa.post(CLAIM.format(request_id=request_id), headers=AUTH)
    assert claimed.status_code == 200
    assert "layers" not in claimed.json()
    finished = qa.post(
        RESULT.format(request_id=request_id), json={"verdicts": ["pass"]}, headers=AUTH
    )
    assert finished.status_code == 200
    assert "layers" not in finished.json()


def test_claim_and_result_of_request_with_layers_return_them(qa: TestClient) -> None:
    response = qa.post(
        REQUESTS, json=_create_body(nonce="n2", layers=["regression", "ui"]), headers=AUTH
    )
    request_id = str(response.json()["id"])
    claimed = qa.post(CLAIM.format(request_id=request_id), headers=AUTH)
    assert claimed.status_code == 200
    assert claimed.json()["layers"] == ["regression", "ui"]
    finished = qa.post(
        RESULT.format(request_id=request_id), json={"verdicts": ["pass"]}, headers=AUTH
    )
    assert finished.status_code == 200
    assert finished.json()["layers"] == ["regression", "ui"]


def test_no_secret_in_any_qa_response(qa: TestClient) -> None:
    bodies: list[str] = []
    covered: set[tuple[str, str]] = set()

    def send(method: str, template: str, path: str, **kwargs: Any) -> Any:
        response = qa.request(method, path, **kwargs)
        covered.add((method, template))
        bodies.append(response.text)
        return response

    created = send("POST", REQUESTS, REQUESTS, json=_create_body(), headers=AUTH)
    assert created.status_code == 200
    request_id = str(created.json()["id"])
    send("POST", REQUESTS, REQUESTS, json={**_create_body(), "mode": "bogus"}, headers=AUTH)
    send("POST", REQUESTS, REQUESTS, json=_create_body(), headers=WRONG_AUTH)
    send("GET", REQUESTS, REQUESTS, headers=AUTH)
    send("GET", REQUESTS, REQUESTS, params={"status": "bogus"}, headers=AUTH)
    send("GET", ONE, ONE.format(request_id=request_id), headers=AUTH)
    send("GET", ONE, ONE.format(request_id="nope"), headers=AUTH)
    send("POST", CLAIM, CLAIM.format(request_id=request_id), headers=WRONG_AUTH)
    claimed = send("POST", CLAIM, CLAIM.format(request_id=request_id), headers=AUTH)
    assert claimed.status_code == 200
    send("POST", CLAIM, CLAIM.format(request_id=request_id), headers=AUTH)
    send("POST", CLAIM, CLAIM.format(request_id="nope"), headers=AUTH)
    result_path = RESULT.format(request_id=request_id)
    send("POST", RESULT, result_path, json={"verdicts": ["maybe"]}, headers=AUTH)
    send("POST", RESULT, result_path, json={"verdicts": ["pass"]}, headers=WRONG_AUTH)
    send("POST", RESULT, RESULT.format(request_id="nope"), json={"verdicts": []}, headers=AUTH)
    finished = send("POST", RESULT, result_path, json={"verdicts": ["pass"]}, headers=AUTH)
    assert finished.status_code == 200

    routes = {
        (method, route.path)
        for route in cast(FastAPI, qa.app).routes
        if isinstance(route, APIRoute) and route.path.startswith(ROUTE_PREFIX)
        for method in route.methods or ()
    }
    assert covered == routes
    for text in bodies:
        assert SECRET not in text
        assert WRONG_BEARER not in text


def test_result_accepts_layer_verdict_objects(qa: TestClient) -> None:
    request_id = _create(qa)
    qa.post(CLAIM.format(request_id=request_id), headers=AUTH)

    response = qa.post(
        RESULT.format(request_id=request_id),
        json={
            "verdicts": [
                {"verdict": "fail", "layer": "api", "failed_reason": "GET /x answered 500"},
                "pass",
            ]
        },
        headers=AUTH,
    )

    assert response.status_code == 200, response.text
    assert response.json()["verdicts"] == [
        {"verdict": "fail", "layer": "api", "failed_reason": "GET /x answered 500"},
        "pass",
    ]


def test_result_refuses_an_unknown_verdict_object(qa: TestClient) -> None:
    request_id = _create(qa)
    qa.post(CLAIM.format(request_id=request_id), headers=AUTH)

    response = qa.post(
        RESULT.format(request_id=request_id),
        json={"verdicts": [{"verdict": "great", "layer": "api"}]},
        headers=AUTH,
    )

    assert response.status_code == 422


def test_a_qa_request_is_recorded_as_a_qa_run(qa: TestClient) -> None:
    request_id = _create(qa)
    run_id = "run_" + request_id
    qa.post(CLAIM.format(request_id=request_id), headers=AUTH)
    opened = qa.get(f"/v1/runs/{run_id}", headers=AUTH).json()
    assert (opened["role"], opened["status"]) == ("qa", "running")

    verdicts = [
        {"verdict": "pass", "scenario": "a"},
        {"verdict": "fail", "scenario": "b"},
        "skipped",
    ]
    body = {"verdicts": verdicts, "error": ""}
    qa.post(RESULT.format(request_id=request_id), json=body, headers=AUTH)
    closed = qa.get(f"/v1/runs/{run_id}", headers=AUTH).json()
    assert closed["status"] == "failed"
    assert "1 fail, 1 pass, 1 skipped" in closed["output"]
    assert "Failed: b" in closed["output"]
    assert closed["cost_usd"] == 0.0


def test_a_clean_qa_request_is_a_completed_run(qa: TestClient) -> None:
    request_id = _create(qa)
    qa.post(CLAIM.format(request_id=request_id), headers=AUTH)
    body = {"verdicts": ["pass", "pass"], "error": ""}
    qa.post(RESULT.format(request_id=request_id), json=body, headers=AUTH)
    run = qa.get(f"/v1/runs/{'run_' + request_id}", headers=AUTH).json()
    assert (run["status"], run["role"]) == ("completed", "qa")
