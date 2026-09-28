"""Where the dashboard reads runs from.

* :class:`RunnerSource` — claude-runner's read-only ``/v1/runs`` API (Claude Code runs of every
  role and provider).
* :class:`HermesSource` — a Hermes Agent API server (``/api/sessions``): AHAWR's Hermes
  sessions, with the model's reasoning, tool calls and tool results as Hermes persisted them.

Both return the same run summaries and activity entries, so the page renders them alike.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from .events import MAX_TEXT, _clip, _content_text, _tool_input
from .titles import role_of, title_of

HERMES_PREFIX = "hermes:"
ACTIVE_SECONDS = 90  # a Hermes session touched this recently counts as running


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), UTC).isoformat(timespec="seconds")
    return str(value)


def _epoch(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


class SourceError(Exception):
    pass


def _check(response: httpx.Response, what: str) -> Any:
    if response.status_code == 404:
        return None
    if response.status_code in (401, 403):
        raise SourceError(f"{what}: {response.status_code} (check the API key)")
    if response.status_code >= 400:
        raise SourceError(f"{what}: HTTP {response.status_code} {response.text[:200]}")
    return response.json()


def _rows(body: Any, key: str) -> list[dict[str, Any]]:
    """List rows of a Hermes response: ``data`` (API server) or ``sessions``/``messages``."""
    if not isinstance(body, dict):
        return []
    rows = body.get("data")
    if rows is None:
        rows = body.get(key)
    return [r for r in rows or [] if isinstance(r, dict)]


class RunnerSource:
    """claude-runner's own run list and activity logs."""

    name = "claude-runner"

    def __init__(self, client: httpx.AsyncClient, url: str, api_key: str = "") -> None:
        self.client = client
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def list_runs(
        self, limit: int, session_id: str, role: str, status: str
    ) -> list[dict[str, Any]]:
        params = {"limit": str(limit), "session_id": session_id, "role": role, "status": status}
        response = await self.client.get(
            f"{self.url}/v1/runs",
            params={k: v for k, v in params.items() if v},
            headers=self.headers,
        )
        body = _check(response, "claude-runner") or {"runs": []}
        runs: list[dict[str, Any]] = body["runs"]
        for run in runs:
            run["source"] = "claude-code"
            run["updated_at"] = run.get("started_at") or run.get("created_at")
        return runs

    async def events(self, run_id: str, after: int) -> dict[str, Any] | None:
        response = await self.client.get(
            f"{self.url}/v1/runs/{run_id}/events", params={"after": after}, headers=self.headers
        )
        body = _check(response, "claude-runner")
        if body:
            body["run"]["source"] = "claude-code"
        return body  # type: ignore[no-any-return]


def _parse_arguments(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"arguments": raw}
    return raw


def _tool_outcome(text: str) -> dict[str, Any]:
    """Hermes tool results are JSON objects (``{"output", "exit_code", "error"}``,
    ``{"content", "total_lines"}``…): the readable part, and whether the call failed."""
    try:
        result = json.loads(text)
    except ValueError:
        return {"is_error": False}
    if not isinstance(result, dict):
        return {"is_error": False}
    exit_code = result.get("exit_code")
    failed = bool(result.get("error")) or (isinstance(exit_code, int) and exit_code != 0)
    for key in ("output", "content", "result", "error"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return {"is_error": failed, "preview": _clip(value, 2000)}
    return {"is_error": failed}


def hermes_entries(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hermes session messages (OpenAI chat format) → dashboard activity entries.

    Each assistant message is one model request (a ``step``). Hermes stores when a message was
    written, so a step's duration is measured from the previous message (the request's input)
    to the answer, and a tool's from its call to its result."""
    out: list[dict[str, Any]] = []
    step = 0
    previous: float | None = None
    for message in messages:
        raw = message.get("timestamp")
        stamp = float(raw) if isinstance(raw, int | float) else None
        at = {"t": stamp} if stamp is not None else {}
        role = message.get("role")
        if message.get("display_kind") == "hidden" or role == "system":
            previous = stamp or previous
            continue
        text = _content_text(message.get("content"))
        if role == "user":
            out.append({"kind": "prompt", "text": _clip(text), **at})
        elif role == "assistant":
            step += 1
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            if reasoning:
                out.append({"kind": "thinking", "text": _clip(str(reasoning)), "step": step, **at})
            if text.strip():
                out.append({"kind": "text", "text": _clip(text), "step": step, **at})
            calls = message.get("tool_calls")
            if isinstance(calls, str):
                calls = _parse_arguments(calls)
            for call in calls if isinstance(calls, list) else []:
                function = call.get("function") or {}
                out.append(
                    {
                        "kind": "tool_use",
                        "id": call.get("id", ""),
                        "name": function.get("name") or call.get("name", ""),
                        "input": _tool_input(_parse_arguments(function.get("arguments"))),
                        "step": step,
                        **at,
                    }
                )
            entry: dict[str, Any] = {
                "kind": "step",
                "step": step,
                "output_tokens": int(message.get("token_count") or 0),
                "finish_reason": message.get("finish_reason") or "",
                "approximate": True,
            }
            if previous is not None and stamp is not None:
                entry["started"] = previous
                entry["duration_ms"] = max(0, round((stamp - previous) * 1000))
                seconds = stamp - previous
                if seconds > 0 and entry["output_tokens"]:
                    entry["tokens_per_s"] = round(entry["output_tokens"] / seconds, 1)
            elif stamp is not None:
                entry["started"] = stamp
            out.append(entry)
        elif role == "tool":
            out.append(
                {
                    "kind": "tool_result",
                    "id": message.get("tool_call_id", ""),
                    "name": message.get("tool_name") or "",
                    **_tool_outcome(text),
                    "text": _clip(text, MAX_TEXT),
                    **at,
                }
            )
        previous = stamp or previous
    return out


class HermesSource:
    """Sessions of a Hermes Agent API server (``API_SERVER_KEY``)."""

    name = "hermes"
    PAGE = 500
    MAX_MESSAGES = 5000

    def __init__(
        self, client: httpx.AsyncClient, url: str, api_key: str = "", session_source: str = ""
    ) -> None:
        self.client = client
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.session_source = session_source
        self._described: dict[str, tuple[str, str]] = {}  # session id → (role, title)
        self._gate = asyncio.Semaphore(6)

    async def _get(self, path: str, **params: Any) -> Any:
        response = await self.client.get(f"{self.url}{path}", params=params, headers=self.headers)
        return _check(response, "Hermes")

    async def _describe(self, session_id: str) -> None:
        """Role and task title from the session's first user message (it never changes)."""
        async with self._gate:
            body = await self._get(
                f"/api/sessions/{session_id}/messages", limit=5, offset=0, order="oldest"
            )
        first = next((m for m in _rows(body, "messages") if m.get("role") == "user"), None)
        if first is not None:
            text = _content_text(first.get("content"))
            self._described[session_id] = (role_of(text), title_of(text))

    def _summary(self, session: dict[str, Any]) -> dict[str, Any]:
        sid = str(session.get("id"))
        role, title = self._described.get(sid, (role_of(session.get("preview") or ""), ""))
        last = session.get("last_active") or session.get("started_at")
        active = not session.get("ended_at") and time.time() - _epoch(last) < ACTIVE_SECONDS
        cost = session.get("actual_cost_usd") or session.get("estimated_cost_usd")
        return {
            "run_id": HERMES_PREFIX + sid,
            "kind": "hermes",
            "source": "hermes",
            "title": title or session.get("title") or session.get("preview") or sid,
            "status": "running" if active else ("completed" if session.get("ended_at") else "idle"),
            "role": role,
            "model": session.get("model") or "",
            "requested_model": session.get("model") or "",
            "provider": "hermes",
            "session_id": sid,
            "claude_session_id": "",
            "cwd": "",
            "created_at": _iso(session.get("started_at")),
            "started_at": _iso(session.get("started_at")),
            "finished_at": _iso(session.get("ended_at")),
            "updated_at": _iso(last),
            "num_turns": session.get("api_call_count"),
            "cost_usd": cost,
            "context_tokens": None,
            "tokens": {
                "input": session.get("input_tokens"),
                "output": session.get("output_tokens"),
                "cache_read": session.get("cache_read_tokens"),
                "cache_write": session.get("cache_write_tokens"),
                "reasoning": session.get("reasoning_tokens"),
            },
            "message_count": session.get("message_count"),
            "tool_call_count": session.get("tool_call_count"),
            "error": None,
            "live": False,
        }

    async def list_runs(
        self, limit: int, session_id: str, role: str, status: str
    ) -> list[dict[str, Any]]:
        if session_id:
            one = await self._get(f"/api/sessions/{session_id}")
            session = (one or {}).get("session") or one
            sessions = [session] if session and session.get("id") else []
        else:
            params: dict[str, Any] = {"limit": min(limit, 200)}
            if self.session_source:
                params["source"] = self.session_source
            body = await self._get("/api/sessions", **params)
            if body is None:
                raise SourceError("Hermes: this version has no /api/sessions (update Hermes)")
            sessions = _rows(body, "sessions")
        missing = [str(s["id"]) for s in sessions if str(s.get("id")) not in self._described]
        await asyncio.gather(*(self._describe(sid) for sid in missing), return_exceptions=True)
        runs = [self._summary(s) for s in sessions]
        return [
            r
            for r in runs
            if (not role or r["role"] == role) and (not status or r["status"] == status)
        ]

    async def _messages(self, sid: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        while len(messages) < self.MAX_MESSAGES:
            body = await self._get(
                f"/api/sessions/{sid}/messages",
                limit=self.PAGE,
                offset=len(messages),
                order="oldest",
            )
            page = _rows(body, "messages")
            messages += page
            if len(page) < self.PAGE:
                break
        return messages

    async def events(self, run_id: str, after: int) -> dict[str, Any] | None:
        sid = run_id.removeprefix(HERMES_PREFIX)
        meta = await self._get(f"/api/sessions/{sid}")
        session = (meta or {}).get("session") or meta
        if not session or not session.get("id"):
            return None
        # Steps are numbered over the whole session, so every poll maps all messages.
        entries = hermes_entries(await self._messages(sid))
        for index, entry in enumerate(entries):
            entry["seq"] = index + 1
        if sid not in self._described:
            await self._describe(sid)
        return {
            "run": self._summary(session),
            "events": entries[after:],
            "next": len(entries),
            "live": None,
        }
