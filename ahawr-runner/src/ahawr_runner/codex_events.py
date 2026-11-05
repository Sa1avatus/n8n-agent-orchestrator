"""Codex ``exec --json`` lines reduced to the runner's event entries.

The mapping is in docs/codex-executor.md, section 1.

The entries use the kinds and fields of ``events.py``, so the dashboard shows a Codex run the way it
shows a Claude run. Lines that are not JSON, and event or item types that are not mapped, are logged
and skipped; they never raise.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from .events import _clip

log = logging.getLogger(__name__)

STEP_KINDS = ("thinking", "text", "tool_use")
TOOL_ITEM_TYPES = ("command_execution", "mcp_tool_call", "file_change", "web_search")
OUTPUT_KEY_BY_ITEM_TYPE = {
    "command_execution": "aggregated_output",
    "mcp_tool_call": "result",
    "web_search": "results",
}
TRANSIENT = "transient"
PERMANENT = "permanent"
CONTEXT_OVERFLOW = "context_overflow"
OTHER = "other"
FAILURE_BY_CODEX_INFO = {
    "rateLimitExceeded": (TRANSIENT, "rate_limit"),
    "serverOverloaded": (TRANSIENT, "overloaded"),
    "internalServerError": (TRANSIENT, "server_error"),
    "responseStreamDisconnected": (TRANSIENT, "server_error"),
    "httpConnectionFailed": (TRANSIENT, "server_error"),
    "responseStreamConnectionFailed": (TRANSIENT, "server_error"),
    "unauthorized": (PERMANENT, "auth_error"),
    "badRequest": (PERMANENT, "api_error"),
    "usageLimitExceeded": (PERMANENT, "usage_limit"),
    "contextWindowExceeded": (CONTEXT_OVERFLOW, "context_overflow"),
    "sandboxError": (OTHER, "sandbox_error"),
}
FAILURE_BY_HTTP_STATUS = {429: (TRANSIENT, "rate_limit"), 529: (TRANSIENT, "overloaded")}
FAILURE_BY_MESSAGE = (
    ("missing environment variable", (PERMANENT, "config_error")),
    ("full_context_window_limit_reached", (CONTEXT_OVERFLOW, "context_overflow")),
    ("token_limit_reached", (CONTEXT_OVERFLOW, "context_overflow")),
)


def _text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _clip(value)
    return _clip(json.dumps(value, ensure_ascii=False))


def _count(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _usage_tokens(usage: dict[str, Any]) -> dict[str, int]:
    cached = _count(usage.get("cached_input_tokens"))
    return {
        "input_tokens": _count(usage.get("input_tokens")) - cached,
        "cache_read_tokens": cached,
        "cache_write_tokens": _count(usage.get("cache_write_input_tokens")),
        "output_tokens": _count(usage.get("output_tokens")),
        "reasoning_output_tokens": _count(usage.get("reasoning_output_tokens")),
    }


def _parse_event(line: str) -> dict[str, Any] | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        log.warning("skipped a Codex output line that is not JSON (%d characters)", len(line))
        return None
    if not isinstance(event, dict):
        log.warning("skipped a Codex output line that is not an object (%d characters)", len(line))
        return None
    return event


def _tool_call(item: dict[str, Any]) -> dict[str, Any]:
    item_type = item.get("type")
    if item_type == "command_execution":
        return {"name": "Bash", "input": {"command": item.get("command", "")}}
    if item_type == "mcp_tool_call":
        name = f"mcp__{item.get('server', '')}__{item.get('tool', '')}"
        return {"name": name, "input": item.get("arguments") or {}}
    if item_type == "web_search":
        return {"name": "WebSearch", "input": {"query": item.get("query", "")}}
    return {"name": "file_change", "input": {"changes": item.get("changes") or []}}


def _tool_outcome(item: dict[str, Any]) -> tuple[bool, str]:
    error = item.get("error")
    is_error = (
        item.get("status") == "failed" or item.get("exit_code") not in (None, 0) or bool(error)
    )
    output_key = OUTPUT_KEY_BY_ITEM_TYPE.get(str(item.get("type")))
    output = error or (item.get(output_key) if output_key else None)
    return is_error, _text_of(output)


def _reasoning_text(item: dict[str, Any]) -> str:
    summary = item.get("summary") or item.get("content") or []
    parts = summary if isinstance(summary, list) else [summary]
    return _clip("\n".join(_text_of(part) for part in parts if part))


def _camel_case(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _match_error(error: dict[str, Any]) -> tuple[str, str] | None:
    info = error.get("codexErrorInfo")
    if isinstance(info, str) and _camel_case(info) in FAILURE_BY_CODEX_INFO:
        return FAILURE_BY_CODEX_INFO[_camel_case(info)]
    status = error.get("httpStatusCode")
    if isinstance(status, int) and status in FAILURE_BY_HTTP_STATUS:
        return FAILURE_BY_HTTP_STATUS[status]
    text = str(error.get("message") or "").lower()
    for needle, failure in FAILURE_BY_MESSAGE:
        if needle in text:
            return failure
    return None


def classify_failure(
    turn_failed: dict[str, Any] | None, exit_code: int | None
) -> tuple[str, str] | None:
    """(failure class, runner status code) of a Codex run, or None when it did not fail.

    ``turn_failed`` is the ``turn.failed`` event (``CodexEventMapper.turn_failed``). Without one, a
    non-zero exit code is ``cli_error``.
    """
    if turn_failed is None:
        return (OTHER, "cli_error") if exit_code not in (None, 0) else None
    error = turn_failed.get("error")
    matched = _match_error(error) if isinstance(error, dict) else None
    return matched or (OTHER, "cli_error")


class CodexEventMapper:
    """Maps one Codex run's JSONL lines, in order, to runner entries.

    ``feed`` returns the entries of one line. The session id, the final message, the usage
    totals and the count of bad lines are read from the attributes after the run.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self.session_id = ""
        self.final_message = ""
        self.usage = dict.fromkeys(_usage_tokens({}), 0)
        self.step_count = 0
        self.bad_line_count = 0
        self.turn_failed: dict[str, Any] | None = None
        self._turn_started: float | None = None
        self._started_item_ids: set[str] = set()

    def feed(self, line: str) -> list[dict[str, Any]]:
        if not line.strip():
            return []
        event = _parse_event(line)
        if event is None:
            self.bad_line_count += 1
            return []
        kind = event.get("type")
        if kind == "thread.started":
            self.session_id = str(event.get("thread_id") or "")
            return []
        if kind == "turn.started":
            self._turn_started = self._clock()
            return []
        if kind == "turn.completed":
            return self._turn_completed(event)
        if kind == "turn.failed":
            self.turn_failed = event
            return []
        if kind in ("item.started", "item.completed"):
            return self._item(event, completed=kind == "item.completed")
        if kind == "error":
            return [
                {
                    "kind": "retry",
                    "attempt": None,
                    "max_retries": None,
                    "status": None,
                    "error": _text_of(event.get("message")),
                    "delay_ms": None,
                }
            ]
        log.debug("Codex event type %r is not mapped; skipped", kind)
        return []

    def _entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        if self._turn_started is not None and entry["kind"] in STEP_KINDS:
            entry["step"] = self.step_count + 1
        return entry

    def _turn_completed(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        usage = event.get("usage")
        tokens = _usage_tokens(usage if isinstance(usage, dict) else {})
        for key, value in tokens.items():
            self.usage[key] += value
        now = self._clock()
        started = self._turn_started if self._turn_started is not None else now
        self._turn_started = None
        self.step_count += 1
        return [
            {
                "kind": "step",
                "step": self.step_count,
                "model": "",
                "started": round(started, 3),
                "duration_ms": round((now - started) * 1000),
                **tokens,
            }
        ]

    def _item(self, event: dict[str, Any], completed: bool) -> list[dict[str, Any]]:
        item = event.get("item")
        if not isinstance(item, dict):
            log.debug("Codex item without an object; skipped")
            return []
        item_type = item.get("type")
        if item_type in TOOL_ITEM_TYPES:
            return self._tool_item(item, completed)
        if not completed:
            return []
        if item_type == "agent_message":
            text = str(item.get("text") or "")
            if not text:
                return []
            self.final_message = text
            return [self._entry({"kind": "text", "text": _clip(text)})]
        if item_type == "reasoning":
            text = _reasoning_text(item)
            return [self._entry({"kind": "thinking", "text": text})] if text else []
        if item_type == "error":
            return [{"kind": "notice", "text": _text_of(item.get("message"))}]
        if item_type == "context_compaction":
            return [
                {
                    "kind": "compact",
                    "trigger": "auto",
                    "pre_tokens": None,
                    "post_tokens": None,
                    "duration_ms": None,
                }
            ]
        log.debug("Codex item type %r is not mapped; skipped", item_type)
        return []

    def _tool_item(self, item: dict[str, Any], completed: bool) -> list[dict[str, Any]]:
        item_id = str(item.get("id") or "")
        call: dict[str, Any] = {"kind": "tool_use", "id": item_id, **_tool_call(item)}
        if not completed:
            self._started_item_ids.add(item_id)
            return [self._entry(call)]
        entries = [] if item_id in self._started_item_ids else [self._entry(call)]
        self._started_item_ids.discard(item_id)
        is_error, text = _tool_outcome(item)
        entries.append({"kind": "tool_result", "id": item_id, "is_error": is_error, "text": text})
        return entries
