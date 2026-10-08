"""Unit tests for the thinking-leak guard (no LiteLLM proxy needed)."""

from __future__ import annotations

import asyncio
import json
import sys
import types

if "litellm" not in sys.modules:
    litellm = types.ModuleType("litellm")
    integrations = types.ModuleType("litellm.integrations")
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")

    class CustomLogger:  # type: ignore[no-redef]
        pass

    custom_logger.CustomLogger = CustomLogger
    integrations.custom_logger = custom_logger
    litellm.integrations = integrations
    sys.modules["litellm"] = litellm
    sys.modules["litellm.integrations"] = integrations
    sys.modules["litellm.integrations.custom_logger"] = custom_logger

from think_leak_guard import (
    ThinkLeakGuard,
    repair_anthropic_sse,
    repair_openai_chunks,
    strip_think_leak,
)


def sse(*events: tuple[str, dict]) -> str:
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)


def text_delta(index: int, text: str) -> tuple[str, dict]:
    return "content_block_delta", {
        "type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}
    }


def test_strip() -> None:
    assert strip_think_leak("plain answer") == ("plain answer", False)
    assert strip_think_leak("let me think\nmore thinking</think>\n\nThe answer.") == ("The answer.", True)
    assert strip_think_leak("The answer.<think>and then it starts again") == ("The answer.", True)
    assert strip_think_leak("a</think>b</think>c") == ("c", True)
    assert strip_think_leak("") == ("", False)


def test_sse_repair_keeps_structure() -> None:
    raw = sse(
        ("message_start", {"type": "message_start"}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        text_delta(0, "I should first look"),
        text_delta(0, " at the file.</think>\n\n"),
        text_delta(0, "Done: the file is fine."),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t1", "name": "Read"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"p\":1}"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        ("message_stop", {"type": "message_stop"}),
    )
    fixed, leak = repair_anthropic_sse(raw)
    assert leak
    assert "I should first look" not in fixed
    assert "Done: the file is fine." in fixed
    assert '"partial_json": "{\\"p\\":1}"' in fixed          # the tool call is untouched
    assert fixed.count("event: content_block_stop") == 2       # all blocks still closed
    assert fixed.count("text_delta") == 1                      # one cleaned delta per text block


def test_sse_without_leak_is_returned_as_is() -> None:
    raw = sse(text_delta(0, "all good"), ("message_stop", {"type": "message_stop"}))
    assert repair_anthropic_sse(raw) == (raw, False)


class Delta:
    def __init__(self, content): self.content = content


class Choice:
    def __init__(self, content): self.delta = Delta(content)


class Chunk:
    def __init__(self, content): self.choices = [Choice(content)]


def test_openai_chunks() -> None:
    chunks = [Chunk("thinking aloud"), Chunk(" here</think>"), Chunk("Real answer")]
    fixed, leak = repair_openai_chunks(chunks)
    assert leak
    assert "".join(c.choices[0].delta.content for c in fixed) == "Real answer"
    clean = [Chunk("a"), Chunk("b")]
    assert repair_openai_chunks(clean)[1] is False


def _run(guard, items, messages=None):
    async def gen():
        for i in items:
            yield i

    async def collect():
        return [x async for x in guard.async_post_call_streaming_iterator_hook(None, gen(), {"model": "m", "messages": messages})]

    return asyncio.run(collect())


def test_guard_end_to_end() -> None:
    guard = ThinkLeakGuard()
    leaked = [sse(text_delta(0, "hmm</think>answer"))]
    out = _run(guard, leaked)
    assert "hmm" not in "".join(out) and "answer" in "".join(out)
    ok = [sse(text_delta(0, "fine"))]
    assert _run(guard, ok) == ok


def test_guard_fails_open_on_garbage() -> None:
    guard = ThinkLeakGuard()
    items = [object(), object()]
    assert _run(guard, items) == items


def test_compaction_request_streams_through() -> None:
    guard = ThinkLeakGuard()
    marker = [{"role": "user", "content": "CRITICAL: Respond with TEXT ONLY ..."}]
    items = [sse(text_delta(0, "x</think>y"))]
    assert _run(guard, items, marker) == items  # untouched (and unbuffered)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
