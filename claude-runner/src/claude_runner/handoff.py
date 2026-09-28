"""Progress digest of a session, for continuing its work in a fresh session.

When a session cannot go on (its context cannot be compacted), the Run Manager starts a new
one. Without a digest the new session only gets the original task and redoes everything; with
it, it learns what was already read, run, found and written. The digest is built from the
activity logs of the session's runs, newest entries kept when it has to be cut.
"""

from __future__ import annotations

import json
from typing import Any

from .runs import RunManager

# per-entry limits: enough to recognise a finding, small enough to keep many steps
TEXT_CHARS = 1500
TOOL_INPUT_CHARS = 300
TOOL_RESULT_CHARS = 400
RESULT_CHARS = 3000
PROMPT_CHARS = 400


def _clip(value: Any, limit: int, one_line: bool = False) -> str:
    text = str(value or "")
    text = " ".join(text.split()) if one_line else text.strip()
    return text if len(text) <= limit else text[:limit] + " …"


def _line(value: Any, limit: int) -> str:
    return _clip(value, limit, one_line=True)


def _tool_input(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("command", "file_path", "path", "pattern", "url", "description"):
            if value.get(key):
                return _line(value[key], TOOL_INPUT_CHARS)
        return _line(json.dumps(value, ensure_ascii=False), TOOL_INPUT_CHARS)
    return _line(value, TOOL_INPUT_CHARS)


def _run_lines(run: dict[str, Any], entries: list[dict[str, Any]], first: bool) -> list[str]:
    started = run.get("started_at") or run.get("created_at") or ""
    head = f"## Run {run['run_id']} ({run['kind']}, {run['status']}, started {started})"
    if run.get("error_code") or run.get("error_message"):
        error = _line(run.get("error_message"), 300)
        head += f" — {run.get('error_code') or 'error'}: {error}"
    lines = [head]
    results = {e.get("id"): e for e in entries if e.get("kind") == "tool_result"}
    for e in entries:
        if e.get("agent"):  # subagent internals: its final answer reaches the parent anyway
            continue
        kind = e.get("kind")
        if kind == "prompt" and not first:
            lines.append(f"PROMPT: {_line(e.get('text'), PROMPT_CHARS)}")
        elif kind == "text" and str(e.get("text") or "").strip():
            lines.append(f"ASSISTANT: {_clip(e.get('text'), TEXT_CHARS)}")
        elif kind == "tool_use":
            name = str(e.get("name") or "tool")
            lines.append(f"TOOL {name}: {_tool_input(e.get('input'))}")
            result = results.get(e.get("id"))
            if result is not None:
                mark = "ERROR " if result.get("is_error") else ""
                lines.append(f"  → {mark}{_line(result.get('text'), TOOL_RESULT_CHARS)}")
        elif kind == "compact":
            lines.append(
                f"[context compacted: {e.get('pre_tokens')} → {e.get('post_tokens')} tokens]"
            )
        elif kind == "result" and str(e.get("text") or "").strip():
            lines.append(f"RESULT: {_clip(e.get('text'), RESULT_CHARS)}")
    return lines


def session_digest(manager: RunManager, session_id: str, max_chars: int) -> dict[str, Any]:
    runs = list(reversed(manager.store.list_runs(500, session_id=session_id)))
    blocks: list[list[str]] = []
    for index, run in enumerate(runs):
        entries, _ = manager.events.read(run["run_id"])
        blocks.append(_run_lines(run, entries, first=index == 0))
    lines = [line for block in blocks for line in block]
    kept: list[str] = []
    size = 0
    for line in reversed(lines):  # newest first until the budget is spent
        if size + len(line) + 1 > max_chars and kept:
            break
        kept.append(line)
        size += len(line) + 1
    kept.reverse()
    omitted = len(lines) - len(kept)
    if omitted:
        kept.insert(0, f"[{omitted} earlier entries omitted]")
    return {
        "session_id": session_id,
        "runs": len(runs),
        "entries": len(lines),
        "truncated": bool(omitted),
        "digest": "\n".join(kept),
    }
