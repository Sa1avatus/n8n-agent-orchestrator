from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ahawr_runner.codex_events import CodexEventMapper, classify_failure

FIXTURES = Path(__file__).parent / "fixtures" / "codex"


def _clock(*ticks: float) -> Callable[[], float]:
    values = iter(ticks)
    return lambda: next(values)


def _feed_fixture(mapper: CodexEventMapper, name: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        entries.extend(mapper.feed(line))
    return entries


def test_success_fixture_maps_to_runner_entries() -> None:
    mapper = CodexEventMapper(clock=_clock(100.0, 102.5))

    entries = _feed_fixture(mapper, "success.jsonl")

    assert entries == [
        {"kind": "thinking", "text": "Read calc.py before editing it.", "step": 1},
        {
            "kind": "tool_use",
            "id": "item_1",
            "name": "Bash",
            "input": {"command": "cat calc.py"},
            "step": 1,
        },
        {
            "kind": "tool_result",
            "id": "item_1",
            "is_error": False,
            "text": "def sub(a, b):\n    return a + b\n",
        },
        {
            "kind": "tool_use",
            "id": "item_2",
            "name": "file_change",
            "input": {"changes": [{"path": "calc.py", "kind": "update"}]},
            "step": 1,
        },
        {"kind": "tool_result", "id": "item_2", "is_error": False, "text": ""},
        {
            "kind": "tool_use",
            "id": "item_3",
            "name": "mcp__docs__lookup",
            "input": {"query": "subtraction"},
            "step": 1,
        },
        {"kind": "tool_result", "id": "item_3", "is_error": False, "text": "no match"},
        {"kind": "text", "text": "Added sub() to calc.py.", "step": 1},
        {
            "kind": "step",
            "step": 1,
            "model": "",
            "started": 100.0,
            "duration_ms": 2500,
            "input_tokens": 400,
            "cache_read_tokens": 800,
            "cache_write_tokens": 0,
            "output_tokens": 90,
            "reasoning_output_tokens": 30,
        },
    ]


def test_success_fixture_reports_session_final_message_and_usage() -> None:
    mapper = CodexEventMapper(clock=_clock(100.0, 102.5))

    _feed_fixture(mapper, "success.jsonl")

    assert mapper.session_id == "0195a1c2-7d3e-7c10-9a4b-2f6e1d8c0a11"
    assert mapper.final_message == "Added sub() to calc.py."
    assert mapper.usage == {
        "input_tokens": 400,
        "cache_read_tokens": 800,
        "cache_write_tokens": 0,
        "output_tokens": 90,
        "reasoning_output_tokens": 30,
    }
    assert mapper.step_count == 1


def test_usage_adds_up_across_turns_of_one_run() -> None:
    mapper = CodexEventMapper(clock=_clock(1.0, 2.0, 3.0, 4.0))

    _feed_fixture(mapper, "success.jsonl")
    entries = _feed_fixture(mapper, "resume.jsonl")

    assert mapper.final_message == "Added a test for sub() as well."
    assert mapper.usage == {
        "input_tokens": 700,
        "cache_read_tokens": 2000,
        "cache_write_tokens": 0,
        "output_tokens": 130,
        "reasoning_output_tokens": 30,
    }
    assert [entry["step"] for entry in entries if entry["kind"] == "step"] == [2]


def test_context_overflow_and_auth_fixtures_map_to_no_entries() -> None:
    for name in ("context_overflow.jsonl", "permanent_auth.jsonl"):
        mapper = CodexEventMapper(clock=_clock(1.0))

        entries = _feed_fixture(mapper, name)

        assert entries == []
        assert mapper.session_id == "0195a1c2-7d3e-7c10-9a4b-2f6e1d8c0a11"
        assert mapper.bad_line_count == 0


def test_rate_limit_fixture_maps_reconnect_notice_to_retry_entry() -> None:
    mapper = CodexEventMapper(clock=_clock(1.0))

    entries = _feed_fixture(mapper, "transient_rate_limit.jsonl")

    assert entries == [
        {
            "kind": "retry",
            "attempt": None,
            "max_retries": None,
            "status": None,
            "error": "Reconnecting... 1/5 (429 Too Many Requests: rate limit reached, retrying)",
            "delay_ms": None,
        }
    ]


def test_started_tool_item_is_not_repeated_when_it_completes() -> None:
    mapper = CodexEventMapper(clock=_clock(1.0))
    started = {
        "type": "item.started",
        "item": {"id": "item_7", "type": "command_execution", "command": "false"},
    }
    completed = {
        "type": "item.completed",
        "item": {
            "id": "item_7",
            "type": "command_execution",
            "command": "false",
            "aggregated_output": "boom",
            "exit_code": 1,
            "status": "failed",
        },
    }

    start_entries = mapper.feed(json.dumps(started))
    done_entries = mapper.feed(json.dumps(completed))

    assert [entry["kind"] for entry in start_entries] == ["tool_use"]
    assert done_entries == [
        {"kind": "tool_result", "id": "item_7", "is_error": True, "text": "boom"}
    ]


def test_error_item_and_compaction_item_map_to_notice_and_compact() -> None:
    mapper = CodexEventMapper(clock=_clock())
    error = {"type": "item.completed", "item": {"id": "e", "type": "error", "message": "no model"}}
    compaction = {"type": "item.completed", "item": {"id": "c", "type": "context_compaction"}}

    assert mapper.feed(json.dumps(error)) == [{"kind": "notice", "text": "no model"}]
    assert mapper.feed(json.dumps(compaction)) == [
        {
            "kind": "compact",
            "trigger": "auto",
            "pre_tokens": None,
            "post_tokens": None,
            "duration_ms": None,
        }
    ]


def test_bad_and_unknown_lines_are_logged_and_counted(caplog: Any) -> None:
    mapper = CodexEventMapper(clock=_clock())

    with caplog.at_level(logging.DEBUG, logger="ahawr_runner.codex_events"):
        assert mapper.feed("not json at all") == []
        assert mapper.feed("[1, 2]") == []
        assert mapper.feed('{"type": "mystery"}') == []
        assert mapper.feed("") == []

    assert mapper.bad_line_count == 2
    messages = [record.getMessage() for record in caplog.records]
    assert any("not JSON" in message for message in messages)
    assert any("not an object" in message for message in messages)
    assert any("'mystery'" in message for message in messages)


def test_failure_fixtures_map_to_their_failure_classes() -> None:
    expected = {
        "context_overflow.jsonl": ("context_overflow", "context_overflow"),
        "transient_rate_limit.jsonl": ("transient", "rate_limit"),
        "permanent_auth.jsonl": ("permanent", "auth_error"),
    }
    for name, failure in expected.items():
        mapper = CodexEventMapper(clock=_clock(1.0))

        _feed_fixture(mapper, name)

        assert classify_failure(mapper.turn_failed, exit_code=1) == failure, name


def test_success_fixture_is_not_a_failure() -> None:
    mapper = CodexEventMapper(clock=_clock(1.0, 2.0))

    _feed_fixture(mapper, "success.jsonl")

    assert classify_failure(mapper.turn_failed, exit_code=0) is None


def test_failure_without_turn_failed_uses_the_exit_code() -> None:
    assert classify_failure(None, exit_code=2) == ("other", "cli_error")
    assert classify_failure(None, exit_code=0) is None
    assert classify_failure(None, exit_code=None) is None


def test_unmatched_turn_failed_is_cli_error() -> None:
    failed = {"type": "turn.failed", "error": {"message": "something new", "codexErrorInfo": "x"}}

    assert classify_failure(failed, exit_code=1) == ("other", "cli_error")


def test_snake_case_error_info_matches_the_camel_case_class() -> None:
    failed = {"type": "turn.failed", "error": {"codexErrorInfo": "rate_limit_exceeded"}}

    assert classify_failure(failed, exit_code=1) == ("transient", "rate_limit")


def test_http_status_and_message_match_when_no_error_info() -> None:
    overloaded = {"type": "turn.failed", "error": {"httpStatusCode": 529}}
    missing_env = {
        "type": "turn.failed",
        "error": {"message": "Missing environment variable OPENAI_API_KEY"},
    }

    assert classify_failure(overloaded, exit_code=1) == ("transient", "overloaded")
    assert classify_failure(missing_env, exit_code=1) == ("permanent", "config_error")


def test_non_integer_http_status_does_not_match() -> None:
    failed = {"type": "turn.failed", "error": {"httpStatusCode": [429]}}

    assert classify_failure(failed, exit_code=1) == ("other", "cli_error")
