from __future__ import annotations

from typing import Any

from claude_runner.engine_events import OTHER, PERMANENT, TRANSIENT
from claude_runner.hermes_events import (
    FALLBACK_KIND,
    classify_failure,
    continue_fields,
    result_entries,
)


def _payload(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "run_1",
        "status": "completed",
        "output": "the answer",
        "poll_count": 3,
    }
    base.update(fields)
    return base


def test_completed_payload_maps_to_text_entry() -> None:
    assert result_entries(_payload(), "run_1", 3) == [{"kind": "text", "text": "the answer"}]


def test_transient_failure_maps_to_retry_entry() -> None:
    entries = result_entries(
        _payload(status="failed", route="transient_error", error="upstream error", retry_count=2),
        "run_1",
        3,
    )
    assert entries == [
        {
            "kind": "retry",
            "attempt": 2,
            "max_retries": None,
            "status": None,
            "error": "upstream error",
            "delay_ms": None,
        }
    ]
    assert classify_failure(_payload(status="failed", route="transient_error"), 200) == (
        TRANSIENT,
        "provider_error",
    )


def test_unmapped_payload_shape_gets_the_fallback_entry() -> None:
    entries = result_entries(_payload(status="weird", output=""), "run_1", 5)
    assert entries == [{"kind": FALLBACK_KIND, "text": "Hermes run run_1: poll 5"}]
    assert classify_failure(_payload(status="weird"), 200) == (OTHER, "unknown")


def test_auth_failure_and_timeout_and_cancelled_map_to_runner_classes() -> None:
    assert classify_failure(_payload(), 401) == (PERMANENT, "auth_error")
    assert classify_failure(_payload(), 403) == (PERMANENT, "auth_error")
    timeout = _payload(status="failed", route="timeout")
    assert classify_failure(timeout, 200) == (TRANSIENT, "timeout")
    assert classify_failure(_payload(status="cancelled", route="cancelled"), 200) == (
        OTHER,
        "cancelled",
    )
    assert classify_failure(_payload(status="completed"), 200) is None
    assert classify_failure(_payload(status="failed", route="failed"), 200) == (
        PERMANENT,
        "provider_error",
    )


def test_handoff_routes_are_not_failures_but_an_unmapped_route_is_unknown() -> None:
    assert classify_failure({"route": "restart_session"}, 200) is None
    assert classify_failure({"route": "start_new"}, 200) is None
    assert classify_failure({"route": "weird"}, 200) == (OTHER, "unknown")


def test_followup_start_sends_the_stored_session_only_on_resume() -> None:
    assert continue_fields("sess_1", "restart_session", "next prompt") == {
        "input": "next prompt",
        "session_id": "sess_1",
    }
    assert continue_fields("sess_1", "start_new", "next prompt") == {"input": "next prompt"}
