"""Codex runs over ``codex app-server`` (JSON-RPC lines on stdio) instead of ``codex exec --json``.

The server streams what ``exec --json`` leaves out: the full reasoning text of every request and
the token usage of every request, cache included (``thread/tokenUsage/updated``, field ``last``).
The notifications are mapped to the same runner entries as the exec events, so the dashboard
shows a Codex run like a Claude Code run. Schema: ``codex app-server generate-json-schema``.

Without ``approval_policy`` / sandbox in the requests, the run's config.toml decides both (the
permissions profile of codex_config.py), as it does for ``exec``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from .claude_cli import UUID_RE
from .codex_events import TOOL_ITEM_TYPES, CodexEventMapper, _tool_call, _usage_tokens

log = logging.getLogger(__name__)

REQUEST_INITIALIZE = 1
REQUEST_THREAD = 2
REQUEST_TURN = 3
CLIENT_NAME = "ahawr-runner"
METHOD_NOT_FOUND = -32601
ITEM_TYPE_BY_SERVER_TYPE = {
    "commandExecution": "command_execution",
    "mcpToolCall": "mcp_tool_call",
    "fileChange": "file_change",
    "webSearch": "web_search",
    "agentMessage": "agent_message",
    "reasoning": "reasoning",
    "contextCompaction": "context_compaction",
    "error": "error",
}
FIELD_BY_SERVER_FIELD = {"aggregatedOutput": "aggregated_output", "exitCode": "exit_code"}
GENERATING_METHODS = ("item/reasoning/textDelta", "item/agentMessage/delta")
LIVE_KIND_BY_ITEM_TYPE = {"reasoning": "thinking", "agent_message": "text"}
LIVE_DELTA_KEY = {"item/reasoning/textDelta": "thinking", "item/agentMessage/delta": "text"}


def stream_event(kind: str, **fields: Any) -> dict[str, Any]:
    """A Claude-style stream event: the live view of the dashboard reads this shape."""
    return {"type": "stream_event", "event": {"type": kind, **fields}}


def exec_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """The server's thread item in the shape of an ``exec --json`` item; None for types the
    runner does not show (the user's own message and the like)."""
    item_type = ITEM_TYPE_BY_SERVER_TYPE.get(str(item.get("type")))
    if item_type is None:
        return None
    mapped = {FIELD_BY_SERVER_FIELD.get(key, key): value for key, value in item.items()}
    mapped["type"] = item_type
    if mapped.get("status") == "declined":
        mapped["status"] = "failed"
    return mapped


class AppServerMapper(CodexEventMapper):
    """``CodexEventMapper`` for server notifications. ``step`` entries come from the token usage
    of each request; their time is approximate and end to end (prompt processing included): from
    the end of the previous tool run (or the turn start) to the last output of the model,
    its tool call included."""

    def __init__(self, model: str, clock: Callable[[], float] = time.time) -> None:
        super().__init__(clock)
        self.model = model
        self.finished = False
        self._request_start: float | None = None
        self._request_end: float | None = None
        self._next_start: float | None = None  # a tool finished before this request's usage came
        self.live_events: list[dict[str, Any]] = []  # for the live view, taken by the run

    def feed_notification(self, method: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if method in ("item/started", "item/completed"):
            return self._server_item(params, completed=method == "item/completed")
        if method in GENERATING_METHODS:
            self._request_end = self._clock()
            self.live_events.append(
                stream_event(
                    "content_block_delta", delta={LIVE_DELTA_KEY[method]: params.get("delta", "")}
                )
            )
        elif method == "thread/tokenUsage/updated":
            return self._request_step(params)
        elif method == "turn/started":
            self._turn_started = self._request_start = self._clock()
        elif method == "turn/completed":
            self._turn_done(params)
        elif method == "error":
            return [self._retry(params)]
        return []

    def _server_item(self, params: dict[str, Any], completed: bool) -> list[dict[str, Any]]:
        raw = params.get("item")
        item = exec_item(raw) if isinstance(raw, dict) else None
        if item is None:
            return []
        now = self._clock()
        self._live_block(item, completed)
        if item["type"] in TOOL_ITEM_TYPES and completed:
            # the next request starts when the tool result is in; the usage of the request that
            # called the tool may arrive before or after this
            if self._request_start is None:
                self._request_start = now
            else:
                self._next_start = now
        elif item["type"] in TOOL_ITEM_TYPES or item["type"] in ("reasoning", "agent_message"):
            self._request_end = now
        return self._item({"item": item}, completed)

    def _live_block(self, item: dict[str, Any], completed: bool) -> None:
        if completed:
            self.live_events.append(stream_event("content_block_stop"))
            return
        kind = LIVE_KIND_BY_ITEM_TYPE.get(item["type"])
        if kind is None and item["type"] in TOOL_ITEM_TYPES:
            block = {"type": "tool_use", "name": _tool_call(item)["name"]}
        else:
            block = {"type": kind} if kind else {}
        if block:
            self.live_events.append(stream_event("content_block_start", content_block=block))

    def _request_step(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        usage = (params.get("tokenUsage") or {}).get("last")
        if not isinstance(usage, dict) or self._turn_started is None:
            # before turn/started the server repeats the last usage of the resumed thread
            return []
        tokens = _usage_tokens(
            {
                "input_tokens": usage.get("inputTokens"),
                "cached_input_tokens": usage.get("cachedInputTokens"),
                "cache_write_input_tokens": usage.get("cacheWriteInputTokens"),
                "output_tokens": usage.get("outputTokens"),
                "reasoning_output_tokens": usage.get("reasoningOutputTokens"),
            }
        )
        for key, value in tokens.items():
            self.usage[key] += value
        now = self._clock()
        started = self._request_start or self._turn_started or now
        ended = self._request_end or now
        self._request_start, self._next_start = self._next_start, None
        self._request_end = None
        self.step_count += 1
        entry: dict[str, Any] = {
            "kind": "step",
            "step": self.step_count,
            "model": self.model,
            "started": round(started, 3),
            "duration_ms": round(max(0.0, ended - started) * 1000),
            "approximate": True,
            **tokens,
        }
        if ended > started and tokens["output_tokens"]:
            entry["generation_ms"] = entry["duration_ms"]
            entry["tokens_per_s"] = round(tokens["output_tokens"] / (ended - started), 1)
        return [entry]

    def _turn_done(self, params: dict[str, Any]) -> None:
        turn = params.get("turn") or {}
        self.finished = True
        if turn.get("status") == "failed":
            self.turn_failed = {"error": turn.get("error") or {}}

    def _retry(self, params: dict[str, Any]) -> dict[str, Any]:
        error = params.get("error") or {}
        return {
            "kind": "retry",
            "attempt": None,
            "max_retries": None,
            "status": None,
            "error": str(error.get("message") or ""),
            "delay_ms": None,
        }


class AppServerSession:
    """The client side of one run: initialize, start or resume the thread, start the turn, then
    feed every server line to the mapper. ``write`` sends one JSON line to the server."""

    def __init__(
        self,
        mapper: AppServerMapper,
        write: Callable[[str], None],
        *,
        cwd: str,
        prompt: str,
        thread_id: str,
        resume: bool,
    ) -> None:
        if resume and not UUID_RE.match(thread_id):
            raise ValueError(f"not a Codex session id: {thread_id!r}")
        self.mapper = mapper
        self._write = write
        self._cwd = cwd
        self._prompt = prompt
        self._thread_id = thread_id if resume else ""
        self.failed = False

    @property
    def done(self) -> bool:
        return self.mapper.finished or self.failed

    def start(self) -> None:
        info = {"name": CLIENT_NAME, "title": CLIENT_NAME, "version": "1"}
        self._request(REQUEST_INITIALIZE, "initialize", {"clientInfo": info})

    def feed(self, line: str) -> list[dict[str, Any]]:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self.mapper.bad_line_count += 1
            log.warning("skipped a Codex server line that is not JSON (%d characters)", len(line))
            return []
        if not isinstance(message, dict):
            return []
        method, message_id = message.get("method"), message.get("id")
        if isinstance(method, str) and message_id is not None:
            return self._decline(message_id, method)
        if isinstance(method, str):
            params = message.get("params")
            return self.mapper.feed_notification(method, params if isinstance(params, dict) else {})
        if message_id is not None:
            self._answered(message_id, message)
        return []

    def server_ended(self) -> None:
        """The process ended: a run without a finished turn failed."""
        if not self.done:
            self._fail("Codex app-server ended before the turn completed.")

    def _request(self, request_id: int, method: str, params: dict[str, Any]) -> None:
        self._send({"id": request_id, "method": method, "params": params})

    def _send(self, message: dict[str, Any]) -> None:
        self._write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")

    def _fail(self, text: str) -> None:
        self.failed = True
        self.mapper.turn_failed = {"error": {"message": text}}

    def _decline(self, message_id: Any, method: str) -> list[dict[str, Any]]:
        # approval_policy is never: a request from the server is a feature the runner has no UI for
        error = {"code": METHOD_NOT_FOUND, "message": f"{method} is not supported by the runner"}
        self._send({"id": message_id, "error": error})
        return [{"kind": "notice", "text": f"Codex asked for {method}; the runner declined."}]

    def _answered(self, message_id: Any, message: dict[str, Any]) -> None:
        error = message.get("error")
        if isinstance(error, dict):
            self._fail(str(error.get("message") or "Codex app-server request failed."))
            return
        result = message.get("result") or {}
        if message_id == REQUEST_INITIALIZE:
            self._send({"method": "initialized"})
            if self._thread_id:
                resume = {"threadId": self._thread_id, "cwd": self._cwd}
                self._request(REQUEST_THREAD, "thread/resume", resume)
            else:
                self._request(REQUEST_THREAD, "thread/start", {"cwd": self._cwd})
        elif message_id == REQUEST_THREAD:
            self.mapper.session_id = str((result.get("thread") or {}).get("id") or "")
            turn: dict[str, Any] = {
                "threadId": self.mapper.session_id,
                "input": [{"type": "text", "text": self._prompt}],
            }
            self._request(REQUEST_TURN, "turn/start", turn)
