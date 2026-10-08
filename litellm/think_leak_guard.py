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


class ThinkLeakGuard(CustomLogger):
    """Buffers a reply until it ends, so a thinking that leaked into the visible text can be cut out
    before Claude Code sees any of it. A step is a few hundred tokens, so streaming is not needed;
    the compaction request (a long summary, minutes of generation) streams through untouched."""

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
        buffered: list[Any] = []
        async for item in response:
            buffered.append(item)
        try:
            if buffered and all(isinstance(i, (str, bytes)) for i in buffered):
                raw = "".join(i if isinstance(i, str) else i.decode("utf-8", "ignore") for i in buffered)
                fixed, leak = repair_anthropic_sse(raw)
                if leak:
                    log.warning("THINK_LEAK model=%s format=sse", request_data.get("model"))
                items = [fixed] if leak else buffered
            else:
                items, leak = repair_openai_chunks(buffered)
                if leak:
                    log.warning("THINK_LEAK model=%s format=chunks", request_data.get("model"))
        except Exception:  # noqa: BLE001 - fail-open
            log.exception("THINK_LEAK guard failed, passing the stream through unchanged")
            items = buffered
        for item in items:
            yield item


think_leak_guard = ThinkLeakGuard()
