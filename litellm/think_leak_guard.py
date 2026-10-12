"""LiteLLM hook: force-close a leaked Qwen thinking.

Qwen3.8-27B sometimes lets its thinking escape the <think>...</think> boundaries; the reasoning
then lands in the visible answer and stays in the context of every later step. This hook
repairs such a reply on its way to Claude Code:

* everything up to the LAST </think> in the visible text is dropped (it was thinking);
* a <think> that never closes drops the rest of that text block;
* tool calls and ordinary replies are never touched.

A reply is buffered only after a think tag has appeared in it; everything else streams through
unchanged. Fail-open: any error passes the stream through as it was. Every repair is logged as
"THINK_LEAK" so the rate can be counted (docker logs ahawr-litellm | grep THINK_LEAK).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

log = logging.getLogger("ahawr.thinkleak")
THINK_TAG = re.compile(r"</?think>")


def strip_think_leak(text: str) -> tuple[str, bool]:
    """(clean text, leak found). Thinking = text before the last </think>, or an unclosed <think>."""
    if not text or not THINK_TAG.search(text):
        return text, False
    last_close = text.rfind("</think>")
    if last_close != -1:
        text = text[last_close + len("</think>"):]
    start = text.find("<think>")
    if start != -1:
        text = text[:start]
    return text.lstrip("\n"), True


def _sse_events(raw: str) -> list[tuple[str, str]]:
    out = []
    for block in raw.split("\n\n"):
        event, data = "", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if event or data:
            out.append((event, data))
    return out


def repair_anthropic_sse(raw: str) -> tuple[str, bool]:
    """Force-close in a whole Anthropic SSE stream held as one string."""
    parsed = []
    texts: dict[int, str] = {}
    for event, data in _sse_events(raw):
        try:
            obj = json.loads(data) if data else {}
        except ValueError:
            obj = {}
        parsed.append((event, obj))
        delta = obj.get("delta") or {}
        if obj.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            index = obj.get("index", 0)
            texts[index] = texts.get(index, "") + str(delta.get("text", ""))
    fixed = {i: strip_think_leak(t) for i, t in texts.items()}
    if not any(leak for _, leak in fixed.values()):
        return raw, False
    done: set[int] = set()
    out = []
    for event, obj in parsed:
        delta = obj.get("delta") or {}
        if obj.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            index = obj.get("index", 0)
            if index in done:
                continue  # the whole cleaned text rides on the first delta of the block
            delta["text"] = fixed[index][0]
            done.add(index)
        out.append(f"event: {event}\ndata: {json.dumps(obj, ensure_ascii=False)}\n\n")
    return "".join(out), True


def repair_openai_chunks(chunks: list[Any]) -> tuple[list[Any], bool]:
    """Force-close for OpenAI-style streaming chunks (choices[0].delta.content)."""
    deltas = []
    for chunk in chunks:
        try:
            delta = chunk.choices[0].delta
        except Exception:  # noqa: BLE001 - not a content chunk
            continue
        if getattr(delta, "content", None):
            deltas.append(delta)
    clean, leak = strip_think_leak("".join(d.content for d in deltas))
    if not leak:
        return chunks, False
    for n, delta in enumerate(deltas):
        delta.content = clean if n == 0 else ""
    return chunks, True


def _chunk_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, bytes):
        return item.decode("utf-8", "ignore")
    try:
        return getattr(item.choices[0].delta, "content", None) or ""
    except Exception:  # noqa: BLE001
        return ""


# A text block is held back only until it is clear that nothing leaked: it is released at once when it grows past
# this many characters without a think tag (a leak is the model's reasoning prose, which ends in </think>).
FLUSH_LIMIT = 1500
NL = chr(10)
EOE = NL + NL  # end of an SSE event


def _sse_event(event: str, obj: dict[str, Any]) -> str:
    return "event: " + event + NL + "data: " + json.dumps(obj, ensure_ascii=False) + EOE


def _text_delta(index: int, text: str) -> str:
    return _sse_event("content_block_delta", {
        "type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}})


class _SseRepair:
    """Incremental repair of an Anthropic SSE stream. Thinking blocks, tool calls and every other event pass
    through immediately (live view, honest timing); only text blocks are held until they end or are known clean."""

    def __init__(self) -> None:
        self.pending = ""
        self.held: dict[int, dict[str, Any]] = {}
        self.leaks = 0

    def feed(self, chunk: str) -> list[str]:
        self.pending += chunk
        parts = self.pending.split(EOE)
        self.pending = parts.pop()  # an incomplete tail waits for the next chunk
        out: list[str] = []
        for raw in parts:
            out.extend(self._event(raw))
        return out

    def finish(self) -> list[str]:
        out: list[str] = []
        if self.pending.strip():
            out.extend(self._event(self.pending))
        self.pending = ""
        for index in sorted(self.held):
            out.extend(self._release(index))
        return out

    def _event(self, raw: str) -> list[str]:
        data = ""
        for line in raw.split(NL):
            if line.startswith("data:"):
                data += line[5:].strip()
        try:
            obj = json.loads(data) if data else {}
        except ValueError:
            return [raw + EOE]
        kind, index = obj.get("type"), obj.get("index", 0)
        if kind == "content_block_start" and (obj.get("content_block") or {}).get("type") == "text":
            self.held[index] = {"start": raw + EOE, "text": "", "flushed": False}
            return []
        hold = self.held.get(index) if kind in ("content_block_delta", "content_block_stop") else None
        if hold is None:
            return [raw + EOE]
        if kind == "content_block_delta":
            delta = obj.get("delta") or {}
            if delta.get("type") != "text_delta" or hold["flushed"]:
                return [raw + EOE]
            hold["text"] += str(delta.get("text", ""))
            if len(hold["text"]) > FLUSH_LIMIT and not THINK_TAG.search(hold["text"]):
                hold["flushed"] = True
                return [hold["start"], _text_delta(index, hold["text"])]
            return []
        out = self._release(index)  # content_block_stop
        out.append(raw + EOE)
        return out

    def _release(self, index: int) -> list[str]:
        hold = self.held.pop(index, None)
        if hold is None or hold["flushed"]:
            return []
        clean, leak = strip_think_leak(hold["text"])
        if leak:
            self.leaks += 1
        out = [hold["start"]]
        if clean:
            out.append(_text_delta(index, clean))
        return out


class ThinkLeakGuard(CustomLogger):
    """Force-closes a Qwen thinking that leaked into the visible text. Thinking blocks and tool calls stream through
    live; a text block is held until it ends (or grows past FLUSH_LIMIT without a think tag), then the leak is cut out.
    The compaction request (a long summary, minutes of generation) streams through untouched."""

    async def async_post_call_streaming_iterator_hook(  # type: ignore[override]
        self, user_api_key_dict: Any, response: Any, request_data: dict[str, Any]
    ):
        from ahawr_hooks import _is_compaction_request

        messages = request_data.get("messages")
        skip = os.environ.get("AHAWR_THINK_GUARD", "1") == "0" or (
            isinstance(messages, list) and _is_compaction_request(messages)
        )
        if skip:
            async for item in response:
                yield item
            return
        repair = _SseRepair()
        passthrough = False
        async for item in response:
            if passthrough or not isinstance(item, (str, bytes)):
                passthrough = True  # not an Anthropic SSE stream: leave it alone
                yield item
                continue
            text = item if isinstance(item, str) else item.decode("utf-8", "ignore")
            for piece in repair.feed(text):
                yield piece
        for piece in repair.finish():
            yield piece
        if repair.leaks:
            log.warning("THINK_LEAK model=%s blocks=%d", request_data.get("model"), repair.leaks)


think_leak_guard = ThinkLeakGuard()
