"""Tests for the qa_agent run client: the body carries role ``qa_agent`` and no
model or provider, polling is bounded, and a timeout or failed status turns
into a FAIL for every item through ``parse_agent_verdicts``."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, cast

import pytest

from claude_runner import qa_agent_run
from claude_runner.qa_agent_verdicts import parse_agent_verdicts

ITEM_IDS = ("C1", "C2")
START = {"run_id": "run-agent-1", "status": "queued"}


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _fake_runner(
    monkeypatch: pytest.MonkeyPatch,
    payloads: list[dict[str, Any]],
    error: Exception | None = None,
) -> list[urllib.request.Request]:
    sent: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        sent.append(request)
        if error is not None:
            raise error
        index = min(len(sent) - 1, len(payloads) - 1)
        return _FakeResponse(payloads[index])

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return sent


def _no_sleep(_seconds: float) -> None:
    pass


def test_the_run_body_carries_role_qa_agent_and_no_model_or_provider(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qa_agent_run.time, "sleep", _no_sleep)
    sent = _fake_runner(
        monkeypatch,
        [START, {"status": "completed", "output": "done"}],
    )

    outcome = qa_agent_run.run_agent("the agent prompt", "/w")

    body = json.loads(cast(bytes, sent[0].data or b""))
    assert body["input"] == "the agent prompt"
    assert body["role"] == "qa_agent"
    assert body["working_directory"] == "/w"
    assert set(body) == {"input", "role", "working_directory"}
    assert sent[0].headers["Content-type"] == "application/json"
    assert sent[1].full_url.endswith("/v1/runs/run-agent-1")
    assert (outcome.status, outcome.final_text) == ("completed", "done")


def test_polling_is_bounded_and_a_still_running_run_times_out(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qa_agent_run.time, "sleep", _no_sleep)
    sent = _fake_runner(
        monkeypatch,
        [START, {"status": "running"}, {"status": "running"}, {"status": "running"}],
    )

    outcome = qa_agent_run.run_agent("p", "/w", max_polls=2, poll_seconds=1)

    assert outcome.status == "timeout" and outcome.final_text == ""
    assert "still running after 2 polls" in outcome.reason
    assert len(sent) == 3
    assert all(request.full_url.endswith("/v1/runs/run-agent-1") for request in sent[1:])


def test_a_failed_run_gives_a_fail_for_every_item(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qa_agent_run.time, "sleep", _no_sleep)
    failed = {
        "status": "failed",
        "output": "",
        "error": {"code": "api_error", "message": "the model refused"},
    }
    _fake_runner(monkeypatch, [START, failed])

    outcome = qa_agent_run.run_agent("p", "/w")

    assert outcome.status == "failed"
    assert outcome.final_text == ""
    assert outcome.reason == "the model refused"
    parsed = parse_agent_verdicts(outcome.final_text, ITEM_IDS)
    assert parsed["gate"] == "fail" and parsed["verdicts"] == []


def test_a_timeout_gives_a_fail_for_every_item(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qa_agent_run.time, "sleep", _no_sleep)
    _fake_runner(monkeypatch, [START, {"status": "running"}, {"status": "running"}])

    outcome = qa_agent_run.run_agent("p", "/w", max_polls=1, poll_seconds=1)

    assert outcome.status == "timeout"
    parsed = parse_agent_verdicts(outcome.final_text, ITEM_IDS)
    assert parsed["gate"] == "fail" and parsed["verdicts"] == []


def test_a_start_error_is_a_failed_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_runner(monkeypatch, [], error=urllib.error.URLError("connection refused"))

    outcome = qa_agent_run.run_agent("p", "/w")

    assert outcome.status == "failed" and outcome.final_text == ""
    assert "could not be started" in outcome.reason
