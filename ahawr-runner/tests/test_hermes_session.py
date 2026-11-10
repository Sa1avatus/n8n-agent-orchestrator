import json
import threading
from typing import Any

import pytest

from ahawr_runner import hermes_session
from ahawr_runner.hermes_run import HermesError
from ahawr_runner.hermes_session import SessionTail

CALL = {"id": "call_1", "function": {"name": "terminal", "arguments": '{"command": "echo 391"}'}}


def message(role: str, ts: float, **fields: Any) -> dict[str, Any]:
    return {"role": role, "timestamp": ts, **fields}


def totals(**values: int) -> dict[str, int]:
    base = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    return {**base, **values}


def test_assistant_message_becomes_reasoning_tool_call_and_answer() -> None:
    tail = SessionTail("qwen")
    entries = tail.feed(
        [
            message("user", 100.0, content="go"),
            message("assistant", 110.0, reasoning_content="think", tool_calls=[CALL]),
            message(
                "tool", 110.5, tool_call_id="call_1", content='{"output": "391", "exit_code": 0}'
            ),
            message("assistant", 113.0, content="391", reasoning="again"),
        ],
        totals(),
    )
    kinds = [(e["kind"], e.get("step")) for e in entries]
    assert kinds == [
        ("thinking", 1),
        ("tool_use", 1),
        ("tool_result", None),
        ("thinking", 2),
        ("text", 2),
    ]
    assert entries[1]["input"] == {"command": "echo 391"} and entries[1]["name"] == "terminal"
    assert entries[2]["is_error"] is False and tail.emitted_text and tail.steps == 2


def test_tool_result_error_is_detected() -> None:
    tail = SessionTail("m")
    bad = message("tool", 1.0, tool_call_id="c", content='{"status": "error", "error": "BLOCKED"}')
    assert tail.feed([bad], totals())[0]["is_error"] is True
    failed = message("tool", 2.0, tool_call_id="c", content='{"output": "", "exit_code": 2}')
    assert tail.feed([failed], totals())[0]["is_error"] is True


def test_step_tokens_wait_for_the_session_totals_and_split_by_request() -> None:
    tail = SessionTail("qwen")
    first = tail.feed(
        [message("user", 100.0), message("assistant", 110.0, tool_calls=[CALL])], totals()
    )
    assert all(e["kind"] != "step" for e in first)
    steps = tail.feed(
        [], totals(input_tokens=10, cache_read_tokens=900, output_tokens=60, cache_write_tokens=4)
    )
    assert len(steps) == 1 and steps[0]["kind"] == "step" and steps[0]["step"] == 1
    assert steps[0]["input_tokens"] == 10 and steps[0]["cache_read_tokens"] == 900
    assert steps[0]["output_tokens"] == 60 and steps[0]["duration_ms"] == 10000
    assert steps[0]["tokens_per_s"] == 6.0 and steps[0]["approximate"] is True
    second = tail.feed(
        [
            message("tool", 111.0, tool_call_id="call_1", content="{}"),
            message("assistant", 112.0, tool_calls=[CALL]),
            message("assistant", 114.0, content="x"),
        ],
        totals(input_tokens=30, cache_read_tokens=1900, output_tokens=160, cache_write_tokens=4),
    )
    got = [e for e in second if e["kind"] == "step"]
    assert [e["step"] for e in got] == [2, 3]
    assert [e["output_tokens"] for e in got] == [50, 50]
    assert [e["duration_ms"] for e in got] == [1000, 2000]


def test_flush_writes_the_waiting_steps_without_tokens() -> None:
    tail = SessionTail("m")
    tail.feed([message("user", 1.0), message("assistant", 2.0, content="a")], totals())
    flushed = tail.flush()
    assert len(flushed) == 1 and flushed[0]["output_tokens"] == 0
    assert "tokens_per_s" not in flushed[0] and tail.flush() == []


def test_messages_older_than_the_run_are_history() -> None:
    tail = SessionTail("m", since=50.0)
    entries = tail.feed(
        [message("assistant", 10.0, content="old"), message("assistant", 60.0, content="new")],
        totals(),
    )
    assert [e["text"] for e in entries if e["kind"] == "text"] == ["new"]
    assert tail.offset == 2 and tail.steps == 1


def test_baseline_excludes_the_resumed_sessions_tokens() -> None:
    tail = SessionTail("m")
    tail.baseline(totals(output_tokens=500))
    tail.feed([message("assistant", 5.0, content="a")], totals())
    steps = tail.feed([], totals(output_tokens=540))
    assert steps[0]["output_tokens"] == 40


def test_fetch_helpers_return_empty_when_the_gateway_has_no_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise HermesError("Hermes: HTTP 404", 404)

    monkeypatch.setattr(hermes_session, "_send", refuse)
    monkeypatch.setattr(hermes_session, "_auth_headers", lambda _p: {})
    assert hermes_session.fetch_messages(object(), "s", 0) == []
    assert hermes_session.fetch_totals(object(), "s") == {}
    assert hermes_session.run_session_id(object(), "r") == ""


def test_watch_polls_once_more_after_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = [message("user", 1.0), message("assistant", 2.0, content="done")]
    monkeypatch.setattr(hermes_session, "fetch_messages", lambda _p, _s, offset: sent[offset:])
    monkeypatch.setattr(hermes_session, "fetch_totals", lambda _p, _s: totals(output_tokens=7))
    stop = threading.Event()
    stop.set()
    seen: list[dict[str, Any]] = []
    tail = SessionTail("m")
    monkeypatch.setattr(hermes_session, "run_session_id", lambda _p, _r: "run_1")
    hermes_session.watch(object(), "run_1", "", tail, seen.append, stop)
    assert [e["kind"] for e in seen] == ["text", "step"]
    assert json.dumps(seen[1]["output_tokens"]) == "7"
