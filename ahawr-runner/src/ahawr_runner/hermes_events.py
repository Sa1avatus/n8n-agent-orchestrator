"""Hermes poll/result payloads reduced to the runner's event entries.

The mapping is in docs/hermes-engine.md (Result shape, Error shape, Session and continue). The
entries use the kinds and fields of `events.py`, so the dashboard shows a Hermes run the way it
shows a Claude run. A payload shape that is not mapped gets the defined fallback entry and is
logged; nothing is dropped silently.

The module stays pure: it writes nothing, reads no store and touches no process registry.
"""

from __future__ import annotations

import logging
from typing import Any

from .engine_events import CONTEXT_OVERFLOW, OTHER, PERMANENT, TRANSIENT, _count, _text_of

log = logging.getLogger(__name__)

RUNNING_ROUTES = ("running", "started", "queued", "waiting_for_approval")
TRANSIENT_HTTP_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
FALLBACK_KIND = "notice"  # the defined entry for a payload shape that is not mapped
FALLBACK_FAILURE = (OTHER, "unknown")
ENTRY_KIND_BY_ROUTE = {
    "completed": "text",
    "failed": FALLBACK_KIND,
    "cancelled": FALLBACK_KIND,
    "canceled": FALLBACK_KIND,
    "run_not_found": FALLBACK_KIND,
    "transient_error": "retry",
    "timeout": FALLBACK_KIND,
    "restart_session": "compact",
    "start_new": "compact",
}
FAILURE_BY_HERMES_STATUS = {401: (PERMANENT, "auth_error"), 403: (PERMANENT, "auth_error")}
# None marks a shape that is not a failure. The keys are the routes of docs/hermes-engine.md.
FAILURE_BY_HERMES_ROUTE = {
    "completed": None,
    "failed": (PERMANENT, "provider_error"),
    "run_not_found": (OTHER, "run_not_found"),
    "transient_error": (TRANSIENT, "provider_error"),
    "permanent_failure": (PERMANENT, "provider_error"),
    "timeout": (TRANSIENT, "timeout"),
    "cancelled": (OTHER, "cancelled"),
    "canceled": (OTHER, "cancelled"),
    "restart_session": None,  # compression handoff routes, not failures
    "start_new": None,
    **dict.fromkeys(RUNNING_ROUTES, None),
}


def route_for(payload: dict[str, Any], http_status: int) -> str:
    """Failure class per the error rules of docs/hermes-engine.md (Evaluate Status): the status
    code, or the numeric code in the body or in the envelope statusCode when the call returned
    200. shortcut: a 4xx/5xx inside the error text is not scanned; upgrade when the gateway
    returns codes only inside the text."""
    code = str(payload.get("error_code") or payload.get("code") or "")
    if http_status == 404 or code in ("run_not_found", "not_found"):
        return "run_not_found"
    codes = [http_status]
    for key in ("statusCode", "code", "http_code"):
        value = payload.get(key)
        if isinstance(value, int):
            codes.append(value)
    if any(code in TRANSIENT_HTTP_CODES for code in codes):
        return "transient_error"
    return "permanent_failure"


def _route_of(payload: dict[str, Any], http_status: int) -> str:
    """Evaluate Status: the route field when the payload carries one, else the code rules."""
    route = str(payload.get("route") or "")
    if route:
        return route
    status = str(payload.get("status") or "")
    return route_for(payload, http_status) if status == "failed" else status


def classify_failure(payload: dict[str, Any], http_status: int | None) -> tuple[str, str] | None:
    """(failure class, runner status code) of a Hermes run, or None when it did not fail.

    `http_status` is the status of the call; when the call returned 200 the code comes from the
    body. Auth (401/403) is checked before the route, compression before the route too. A route
    that is not in the mapping of docs/hermes-engine.md is the unknown failure shape.
    """
    status = http_status if isinstance(http_status, int) else _count(payload.get("http_code"))
    matched = FAILURE_BY_HERMES_STATUS.get(status)
    if matched:
        return matched
    route = _route_of(payload, status)
    if payload.get("compression_failed"):
        return (CONTEXT_OVERFLOW, "context_overflow")
    return FAILURE_BY_HERMES_ROUTE.get(route, FALLBACK_FAILURE)


def _error_text(payload: dict[str, Any]) -> str:
    for key in ("error", "message", "details", "detail"):
        value = payload.get(key)
        if value:
            return _text_of(value)
    return ""


def result_entries(payload: dict[str, Any], run_id: str, poll_count: int) -> list[dict[str, Any]]:
    """Runner entries for one Hermes poll/result payload (Build Result, docs/hermes-engine.md)."""
    route = str(payload.get("route") or payload.get("status") or "")
    kind = ENTRY_KIND_BY_ROUTE.get(route, FALLBACK_KIND)
    text = _text_of(payload.get("output")) if kind == "text" else _error_text(payload)
    if kind == "retry":
        return [
            {
                "kind": "retry",
                "attempt": _count(payload.get("retry_count")),
                "max_retries": None,
                "status": _count(payload.get("http_code")) or None,
                "error": text,
                "delay_ms": None,
            }
        ]
    if kind == "compact":
        return [
            {
                "kind": "compact",
                "trigger": "handoff",
                "pre_tokens": None,
                "post_tokens": None,
                "duration_ms": None,
            }
        ]
    if route in RUNNING_ROUTES:
        log.debug("Hermes route %r has no entry: the poll was still running", route)
        return []
    if not text:
        log.warning("Hermes payload with route %r has no text; fallback entry", route)
        text = f"Hermes run {run_id}: poll {poll_count}"
    return [{"kind": kind, "text": text}]


def usage_step(
    usage: dict[str, Any], model: str, generation_ms: int = 0
) -> dict[str, Any] | None:
    """One ``step`` entry with the gateway's token totals of the run (it reports no per-request
    usage), or None when the gateway sent no tokens. ``generation_ms`` is the run's time
    outside tools: the speed is end to end (prompt processing included)."""
    input_tokens = _count(usage.get("input_tokens"))
    output_tokens = _count(usage.get("output_tokens"))
    if not (input_tokens or output_tokens):
        return None
    step: dict[str, Any] = {
        "kind": "step",
        "step": 1,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    if generation_ms > 0 and output_tokens:
        step["generation_ms"] = generation_ms
        step["tokens_per_s"] = round(output_tokens / (generation_ms / 1000), 1)
    return step


def live_entry(event: dict[str, Any], tool_count: int) -> dict[str, Any] | None:
    """The runner entry of a gateway tool event (``tool.started`` / ``tool.completed``);
    other events have none. ``tool_count`` pairs a result with its call."""
    name = event.get("event")
    if name == "tool.started":
        return {
            "kind": "tool_use",
            "id": f"hermes-{tool_count}",
            "name": str(event.get("tool") or ""),
            "input": {"preview": _text_of(event.get("preview"))},
        }
    if name == "tool.completed":
        return {
            "kind": "tool_result",
            "id": f"hermes-{tool_count}",
            "is_error": bool(event.get("error")),
            "text": "",
        }
    if name == "reasoning.available":
        text = _text_of(event.get("text"))
        return {"kind": "thinking", "text": text} if text else None
    return None


def continue_fields(session_id: str, route: str, input_text: str) -> dict[str, Any]:
    """The body fields of the follow-up start, per the continue fields of docs/hermes-engine.md.

    The stored session id is sent when the route resumes the stored session, and never when the
    route starts a new session from a compression handoff.
    """
    fields: dict[str, Any] = {"input": input_text}
    if session_id and route in ("", "restart_session"):
        fields["session_id"] = session_id
    return fields
