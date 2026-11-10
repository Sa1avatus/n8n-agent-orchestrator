"""Live detail of a Hermes run from the gateway's own session API (``GET
/api/sessions/{id}/messages`` and ``GET /api/sessions/{id}``). The run events stream carries no
reasoning and no per-request usage; the session holds every assistant message with its full
``reasoning_content``, its tool calls and the tool results as they happen, and the token totals
(cache included) of the session.

The messages become runner entries, one ``step`` per assistant message (= one model request). The
tokens of a step are the growth of the session totals since the last step; a request's time is the
span since the message before it, so the speed is approximate (prompt processing included).
Works with any model behind Hermes; no engine telemetry is needed.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from .events import _clip
from .hermes_run import HermesError, _auth_headers, _send

log = logging.getLogger(__name__)

POLL_SECONDS = 2.0
PAGE_MESSAGES = 500
TOTAL_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def fetch_messages(profile: Any, session_id: str, offset: int) -> list[dict[str, Any]]:
    """The session's messages from ``offset`` on, oldest first; [] while the session does not
    exist yet or the gateway does not answer (the live detail is never needed for the result)."""
    path = f"/api/sessions/{session_id}/messages?order=oldest&limit={PAGE_MESSAGES}&offset={offset}"
    try:
        body = _send(profile, "GET", path, None, _auth_headers(profile))
    except HermesError as exc:
        log.debug("Hermes session messages unavailable: %s", exc)
        return []
    data = body.get("data")
    return [m for m in data if isinstance(m, dict)] if isinstance(data, list) else []


def fetch_totals(profile: Any, session_id: str) -> dict[str, int]:
    try:
        body = _send(profile, "GET", f"/api/sessions/{session_id}", None, _auth_headers(profile))
    except HermesError as exc:
        log.debug("Hermes session totals unavailable: %s", exc)
        return {}
    session = body.get("session")
    if not isinstance(session, dict):
        return {}
    return {key: int(session.get(key) or 0) for key in TOTAL_FIELDS}


def run_session_id(profile: Any, run_id: str) -> str:
    try:
        body = _send(profile, "GET", f"/v1/runs/{run_id}", None, _auth_headers(profile))
    except HermesError as exc:
        log.debug("Hermes run %s has no session yet: %s", run_id, exc)
        return ""
    return str(body.get("session_id") or "")


def _function(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function")
    return function if isinstance(function, dict) else {}


def _arguments(call: dict[str, Any]) -> dict[str, Any]:
    raw = _function(call).get("arguments", call.get("arguments"))
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else None
    except json.JSONDecodeError:
        parsed = None
    return parsed if isinstance(parsed, dict) else {"arguments": str(raw or "")}


def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    raw = message.get("tool_calls")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def _tool_name(call: dict[str, Any]) -> str:
    return str(_function(call).get("name") or call.get("name") or "")


def _tool_result(message: dict[str, Any]) -> dict[str, Any]:
    text = str(message.get("content") or "")
    is_error = False
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        is_error = bool(parsed.get("error")) or parsed.get("exit_code") not in (None, 0)
        is_error = is_error or parsed.get("status") == "error"
    return {
        "kind": "tool_result",
        "id": str(message.get("tool_call_id") or ""),
        "is_error": is_error,
        "text": _clip(text),
    }


class SessionTail:
    """Turns the messages and token totals seen while one run lives into entries, once each."""

    def __init__(self, model: str, since: float = 0.0) -> None:
        self.model = model
        self.since = since  # messages older than the run belong to the resumed session's history
        self.offset = 0
        self.steps = 0
        self.emitted_text = False
        self._last_ts = 0.0
        self._totals = dict.fromkeys(TOTAL_FIELDS, 0)
        self._pending: list[tuple[int, float, float]] = []  # (step, started, ended)

    def feed(self, messages: list[dict[str, Any]], totals: dict[str, int]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for message in messages:
            if float(message.get("timestamp") or 0) >= self.since:
                entries += self._message(message)
        self.offset += len(messages)
        return entries + self._settle(totals)

    def baseline(self, totals: dict[str, int]) -> None:
        """The totals a resumed session already had when the run started."""
        self._totals = {k: totals.get(k, 0) for k in TOTAL_FIELDS}

    def flush(self) -> list[dict[str, Any]]:
        """The steps still waiting for token totals, at the end of the run."""
        return self._settle(None)

    def _message(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        ts = float(message.get("timestamp") or 0)
        role = message.get("role")
        if role == "assistant":
            return self._assistant(message, ts)
        if ts:
            self._last_ts = ts
        return [_tool_result(message)] if role == "tool" else []

    def _assistant(self, message: dict[str, Any], ts: float) -> list[dict[str, Any]]:
        self.steps += 1
        stamp = {"step": self.steps}
        self._pending.append((self.steps, self._last_ts or ts, ts))
        self._last_ts = ts or self._last_ts
        entries: list[dict[str, Any]] = []
        thought = str(message.get("reasoning_content") or message.get("reasoning") or "").strip()
        if thought:
            entries.append({"kind": "thinking", "text": _clip(thought), **stamp})
        for call in _tool_calls(message):
            entries.append(
                {
                    "kind": "tool_use",
                    "id": str(call.get("id") or call.get("call_id") or ""),
                    "name": _tool_name(call),
                    "input": _arguments(call),
                    **stamp,
                }
            )
        text = str(message.get("content") or "").strip()
        if text:
            self.emitted_text = True
            entries.append({"kind": "text", "text": _clip(text), **stamp})
        return entries

    def _settle(self, totals: dict[str, int] | None) -> list[dict[str, Any]]:
        if not self._pending:
            return []
        grown = dict.fromkeys(TOTAL_FIELDS, 0)
        if totals is not None:
            grown = {k: totals.get(k, 0) - self._totals[k] for k in TOTAL_FIELDS}
            if not any(value > 0 for value in grown.values()):
                return []  # the totals of these requests are not written yet
            self._totals = {k: max(self._totals[k], totals.get(k, 0)) for k in TOTAL_FIELDS}
        count = len(self._pending)
        steps = [self._step(item, grown, count) for item in self._pending]
        self._pending = []
        return steps

    def _step(
        self, pending: tuple[int, float, float], grown: dict[str, int], count: int
    ) -> dict[str, Any]:
        step, started, ended = pending
        output = max(0, grown["output_tokens"]) // count
        entry: dict[str, Any] = {
            "kind": "step",
            "step": step,
            "model": self.model,
            "started": round(started, 3),
            "duration_ms": round(max(0.0, ended - started) * 1000),
            "approximate": True,
            "input_tokens": max(0, grown["input_tokens"]) // count,
            "cache_read_tokens": max(0, grown["cache_read_tokens"]) // count,
            "cache_write_tokens": max(0, grown["cache_write_tokens"]) // count,
            "output_tokens": output,
        }
        if output and ended > started:
            entry["generation_ms"] = entry["duration_ms"]
            entry["tokens_per_s"] = round(output / (ended - started), 1)
        return entry


def watch(
    profile: Any,
    run_id: str,
    fallback_session: str,
    tail: SessionTail,
    on_entry: Callable[[dict[str, Any]], None],
    stop: threading.Event,
) -> None:
    """Poll the run's session until ``stop`` is set, then once more so nothing is lost."""
    session_id = fallback_session
    if session_id:
        tail.baseline(fetch_totals(profile, session_id))
    while True:
        stopped = stop.is_set()
        session_id = session_id or run_session_id(profile, run_id)
        if session_id:
            messages = fetch_messages(profile, session_id, tail.offset)
            for entry in tail.feed(messages, fetch_totals(profile, session_id)):
                on_entry(entry)
        if stopped:
            for entry in tail.flush():
                on_entry(entry)
            return
        stop.wait(POLL_SECONDS)
