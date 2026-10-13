"""Diagnostics for the llama.cpp prompt cache: where does a request stop matching the previous request of the same conversation?

Off by default (AHAWR_CACHE_TRACE=1 turns it on). For every request to a local model the hook gives this module the final
message list (what llama.cpp renders). The module keeps the previous request of the same conversation and writes one JSON line
to AHAWR_TRACE_DIR/cache_trace.jsonl with the index of the first message that differs, why it differs and how many characters
from there to the end have to be evaluated again (about a third of that in tokens). A request that only extends the previous one
has first_diff = null. It never changes the request and never raises.

Conversation key: a hash of the first system message and the first user message (one Worker session = one key).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from typing import Any

TRACE_DIR = os.environ.get("AHAWR_TRACE_DIR", "/app/trace")
MAX_BYTES = 50 * 1024 * 1024
KEEP = 8  # conversations remembered

_lock = threading.Lock()
_prev: OrderedDict[str, list[str]] = OrderedDict()


def _strip(value: Any) -> Any:
    """cache_control markers move from request to request and are never rendered into the prompt: ignore them."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _flat(message: Any) -> Any:
    """A content list of plain text blocks renders like the string of their texts: compare it as that string."""
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        blocks = message["content"]
        if blocks and all(isinstance(b, dict) and b.get("type", "text") == "text" and set(b) <= {"type", "text"} for b in blocks):
            return {**message, "content": "".join(str(b.get("text", "")) for b in blocks)}
    return message


def _ser(message: Any) -> str:
    try:
        return json.dumps(_flat(_strip(message)), sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return repr(message)


def _kind(message: Any) -> str:
    if not isinstance(message, dict):
        return "other"
    content = message.get("content")
    kinds = set()
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                kinds.add(str(block.get("type", "?")))
    elif isinstance(content, str):
        kinds.add("text")
    if message.get("tool_calls"):
        kinds.add("tool_calls")
    if message.get("reasoning_content"):
        kinds.add("reasoning")
    return f"{message.get('role', '?')}:{'+'.join(sorted(kinds)) or 'empty'}"


def _key(serialized: list[str], tools: Any, model: str) -> str:
    base = "|".join(serialized[:2]) + "|" + model
    return hashlib.sha1(base.encode("utf-8", "replace")).hexdigest()[:12]


def record(messages: list[Any], tools: Any = None, model: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Compare with the previous request of the same conversation and append a trace line. Returns the line (for tests)."""
    try:
        ser = [_ser(m) for m in messages]
        tools_hash = hashlib.sha1(_ser(tools).encode("utf-8", "replace")).hexdigest()[:8] if tools else ""
        key = _key(ser, tools, model)
        with _lock:
            prev = _prev.pop(key, None)
            _prev[key] = ser + ["\x00tools:" + tools_hash]
            while len(_prev) > KEEP:
                _prev.popitem(last=False)
        line: dict[str, Any] = {
            "t": round(time.time(), 1),
            "conv": key,
            "model": model,
            "n": len(ser),
            "chars": sum(map(len, ser)),
            "tools": tools_hash,
        }
        if extra:
            line.update(extra)
        if prev is None:
            line["first_diff"] = "new conversation"
        else:
            prev_msgs, prev_tools = prev[:-1], prev[-1]
            line["prev_n"] = len(prev_msgs)
            line["tools_changed"] = prev_tools != "\x00tools:" + tools_hash
            limit = min(len(prev_msgs), len(ser))
            idx = next((i for i in range(limit) if prev_msgs[i] != ser[i]), None)
            if idx is None:
                line["first_diff"] = None  # pure extension (or the new request is shorter and equal so far)
                line["shrunk"] = len(ser) < len(prev_msgs)
            else:
                a, b = prev_msgs[idx], ser[idx]
                at = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
                line["first_diff"] = idx
                line["kind_new"] = _kind(messages[idx])
                line["at_char"] = at
                line["old"] = a[max(0, at - 40): at + 80]
                line["new"] = b[max(0, at - 40): at + 80]
                line["redo_chars"] = sum(len(x) for x in ser[idx:]) - at
                line["redo_tokens_est"] = line["redo_chars"] // 3
                line["from_end"] = len(ser) - idx
        _write(line)
        return line
    except Exception:
        return None


def _write(line: dict[str, Any]) -> None:
    os.makedirs(TRACE_DIR, exist_ok=True)
    path = os.path.join(TRACE_DIR, "cache_trace.jsonl")
    try:
        if os.path.getsize(path) > MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
