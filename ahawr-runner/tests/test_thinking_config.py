from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ahawr_runner.runs import RunManager, StartRequest
from ahawr_runner.thinking_config import DEFAULTS, flatten, validate

CASES: dict[str, Any] = json.loads(
    (Path(__file__).parent / "thinking_cases.json").read_text(encoding="utf-8")
)


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[StartRequest]:
    seen: list[StartRequest] = []

    async def start(_self: RunManager, request: StartRequest) -> dict[str, Any]:
        seen.append(request)
        return {"run_id": "spy"}

    monkeypatch.setattr(RunManager, "start", start)
    return seen


def test_run_json_value_wins_over_legacy_body(
    client: TestClient, started: list[StartRequest]
) -> None:
    response = client.post(
        "/v1/runs",
        json={
            "input": "TASK T001 hello",
            "thinking_json": '{"from_task": 2, "off_after_seconds": 1800, "off_after_steps": 40}',
            "think_policy_from_task": 1,
            "think_off_after_seconds": 600,
            "think_off_after_steps": 30,
        },
    )
    assert response.status_code == 200, response.text
    request = started[-1]
    assert (request.think_policy_from_task, request.think_off_after_seconds) == (2, 1800)
    assert request.think_off_after_steps == 40


def test_run_legacy_value_used_when_json_key_missing(
    client: TestClient, started: list[StartRequest]
) -> None:
    response = client.post(
        "/v1/runs",
        json={
            "input": "TASK T001 hello",
            "thinking_json": '{"from_task": 2}',
            "think_policy": "off_first",
            "think_off_after_seconds": 600,
            "think_off_after_steps": 30,
        },
    )
    assert response.status_code == 200, response.text
    request = started[-1]
    assert request.think_policy_from_task == 2
    assert (request.think_policy, request.think_off_after_seconds) == ("off_first", 600)
    assert request.think_off_after_steps == 30


def test_run_empty_json_keeps_legacy_body(client: TestClient, started: list[StartRequest]) -> None:
    response = client.post(
        "/v1/runs",
        json={
            "input": "TASK T001 hello",
            "thinking_json": "",
            "think_policy": "off_first",
            "think_policy_from_task": 1,
            "think_off_after_seconds": 600,
            "think_off_after_steps": 30,
        },
    )
    assert response.status_code == 200, response.text
    request = started[-1]
    assert (request.think_policy, request.think_policy_from_task) == ("off_first", 1)
    assert (request.think_off_after_seconds, request.think_off_after_steps) == (600, 30)


def test_run_invalid_json_is_rejected_before_start(
    client: TestClient, started: list[StartRequest]
) -> None:
    response = client.post(
        "/v1/runs", json={"input": "TASK T001 hello", "thinking_json": '{"from_task": 0}'}
    )
    assert response.status_code == 422, response.text
    assert started == []


@pytest.mark.parametrize("case", CASES["parse"], ids=lambda case: case["name"])
def test_shared_parse_case(case: dict[str, Any]) -> None:
    expect = case["expect"]
    assert validate(case["thinking_json"]) == expect["errors"]
    if expect["errors"]:
        with pytest.raises(ValueError):
            flatten(case["thinking_json"], case["legacy"])
        return
    flat = flatten(case["thinking_json"], case["legacy"])
    if "flatten_equals" in expect:
        assert flat == CASES["legacy_constants"]
    else:
        assert {key: flat[key] for key in expect["flatten_partial"]} == expect["flatten_partial"]


@pytest.mark.parametrize("thinking_json", [None, "", "{}"])
def test_empty_json_flattens_to_legacy_constants(thinking_json: str | None) -> None:
    assert flatten(thinking_json, {}) == CASES["legacy_constants"]


def test_defaults_are_the_legacy_constants() -> None:
    assert CASES["legacy_constants"] == DEFAULTS
