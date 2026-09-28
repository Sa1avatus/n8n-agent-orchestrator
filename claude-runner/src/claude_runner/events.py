"""Per-run activity log for the dashboard: what the agent thinks, says and does.

The Claude Code stream (``stream-json``) is reduced to readable entries — thinking, text,
tool calls and their results, retries, compaction, the final result — and appended to
``<data>/events/<run_id>.jsonl``. The same shape comes out whatever model served the run
(Anthropic, or a local / third-party model behind LiteLLM, whose ``reasoning_content``
reaches Claude Code as thinking blocks). Token deltas (``--include-partial-messages``) are
not stored: they only feed the *live* view of the block being generated.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import IO, Any

MAX_TEXT = 20_000
MAX_INPUT = 8_000


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [{len(text) - limit} more characters]"


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return "" if content is None else json.dumps(content, ensure_ascii=False)


def _tool_input(value: Any) -> Any:
    raw = json.dumps(value, ensure_ascii=False)
    if len(raw) <= MAX_INPUT:
        return value
    if isinstance(value, dict):  # keep the keys, clip the long values (Write content, …)
        return {k: _clip(v, MAX_INPUT // 2) if isinstance(v, str) else v for k, v in value.items()}
    return _clip(raw, MAX_INPUT)


def entries_from(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Readable entries for one stream-json event (none for bookkeeping events)."""
    kind, subtype = event.get("type"), event.get("subtype")
    agent = event.get("parent_tool_use_id")  # set for subagent (Task tool) activity
    out: list[dict[str, Any]] = []
    if kind == "assistant":
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "thinking":
                out.append({"kind": "thinking", "text": _clip(str(block.get("thinking", "")))})
            elif btype == "redacted_thinking":
                out.append({"kind": "thinking", "text": "[redacted by the provider]"})
            elif btype == "text":
                out.append({"kind": "text", "text": _clip(str(block.get("text", "")))})
            elif btype in ("tool_use", "server_tool_use"):
                out.append(
                    {
                        "kind": "tool_use",
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "input": _tool_input(block.get("input")),
                    }
                )
    elif kind == "user":
        content = (event.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else [content]
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                out.append(
                    {
                        "kind": "tool_result",
                        "id": block.get("tool_use_id", ""),
                        "is_error": bool(block.get("is_error")),
                        "text": _clip(_content_text(block.get("content"))),
                    }
                )
            elif isinstance(block, dict) and block.get("type") == "text":
                out.append({"kind": "user", "text": _clip(str(block.get("text", "")))})
            elif isinstance(block, str) and block.strip():
                out.append({"kind": "user", "text": _clip(block)})
    elif kind == "system" and subtype == "init":
        out.append(
            {
                "kind": "init",
                "model": event.get("model", ""),
                "cwd": event.get("cwd", ""),
                "permission_mode": event.get("permissionMode", ""),
                "tools": list(event.get("tools") or []),
                "version": event.get("claude_code_version", ""),
            }
        )
    elif kind == "system" and subtype == "api_retry":
        out.append(
            {
                "kind": "retry",
                "attempt": event.get("attempt"),
                "max_retries": event.get("max_retries"),
                "status": event.get("error_status"),
                "error": event.get("error"),
                "delay_ms": event.get("retry_delay_ms"),
            }
        )
    elif kind == "system" and subtype == "compact_boundary":
        meta = event.get("compact_metadata") or {}
        out.append(
            {
                "kind": "compact",
                "trigger": meta.get("trigger"),
                "pre_tokens": meta.get("pre_tokens"),
                "post_tokens": meta.get("post_tokens"),
            }
        )
    elif kind == "result":
        out.append(
            {
                "kind": "result",
                "subtype": event.get("subtype"),
                "is_error": bool(event.get("is_error")),
                "text": _clip(str(event.get("result") or "")),
                "num_turns": event.get("num_turns"),
                "cost_usd": event.get("total_cost_usd"),
                "duration_ms": event.get("duration_ms"),
                "usage": event.get("usage"),
            }
        )
    if agent:
        for entry in out:
            entry["agent"] = agent
    return out


class LiveBlock:
    """The content block being generated right now, from token deltas."""

    def __init__(self) -> None:
        self.block: dict[str, Any] | None = None

    def feed(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "assistant":  # the finished block arrives as a full message
            self.block = None
            return
        if kind != "stream_event":
            return
        inner = event.get("event") or {}
        itype = inner.get("type")
        if itype == "content_block_start":
            start = inner.get("content_block") or {}
            btype = str(start.get("type", ""))
            self.block = {
                "kind": "tool_use" if btype.endswith("tool_use") else btype,
                "name": start.get("name", ""),
                "text": "",
                "agent": event.get("parent_tool_use_id"),
                "started": time.time(),
            }
        elif itype == "content_block_delta" and self.block is not None:
            delta = inner.get("delta") or {}
            piece = delta.get("thinking") or delta.get("text") or delta.get("partial_json") or ""
            if piece:
                # keep the newest tokens: the view follows the generation
                self.block["text"] = (self.block["text"] + str(piece))[-MAX_TEXT:]
        elif itype in ("content_block_stop", "message_stop"):
            self.block = None


class EventLog:
    """Entries of active runs stay in memory; every run's entries are also on disk."""

    def __init__(self, directory: Path, retention_days: int = 14) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._entries: dict[str, list[dict[str, Any]]] = {}
        self._files: dict[str, IO[str]] = {}
        self._live: dict[str, LiveBlock] = {}
        if retention_days > 0:
            self.prune(retention_days)

    def _path(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
            raise ValueError(f"invalid run id: {run_id!r}")
        return self.directory / f"{run_id}.jsonl"

    def prune(self, retention_days: int) -> int:
        cutoff = time.time() - retention_days * 86400
        removed = 0
        for path in self.directory.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:  # pragma: no cover - raced with another process
                pass
        return removed

    def open(self, run_id: str) -> None:
        with self._lock:
            if run_id in self._entries:
                return
            path = self._path(run_id)
            self._entries[run_id] = self._read_file(path)
            self._files[run_id] = path.open("a", encoding="utf-8")
            self._live[run_id] = LiveBlock()

    def add(self, run_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            entries = self._entries.get(run_id)
            if entries is None:
                return
            entry = {"seq": len(entries) + 1, "t": round(time.time(), 3), **entry}
            entries.append(entry)
            handle = self._files[run_id]
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()

    def feed(self, run_id: str, event: dict[str, Any]) -> None:
        live = self._live.get(run_id)
        if live is not None:
            live.feed(event)
        for entry in entries_from(event):
            self.add(run_id, entry)

    def close(self, run_id: str) -> None:
        with self._lock:
            handle = self._files.pop(run_id, None)
            if handle:
                handle.close()
            self._entries.pop(run_id, None)
            self._live.pop(run_id, None)

    def is_live(self, run_id: str) -> bool:
        return run_id in self._live

    def read(
        self, run_id: str, after: int = 0
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        with self._lock:
            entries = self._entries.get(run_id)
            live = self._live.get(run_id)
            if entries is not None:
                block = dict(live.block) if live and live.block else None
                return [e for e in entries[after:]], block
        try:
            path = self._path(run_id)
        except ValueError:
            return [], None
        return self._read_file(path)[after:], None

    @staticmethod
    def _read_file(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        entries = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:  # a line cut by a crash
                    continue
        return entries
