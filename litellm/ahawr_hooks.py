"""LiteLLM proxy hooks for claude-runner → local OpenAI-compatible models.

Claude Code sends some context (e.g. the "# Environment" block) as ``role: "system"`` messages
in the middle of ``messages``. Many local chat templates (Qwen, Llama, Gemma ...) accept a
system message only at the very beginning and fail with e.g. "System message must be at the
beginning". This hook turns every such mid-conversation system message into a
``<system-reminder>`` text block of a user message (merged into the preceding user message when
there is one, so user/assistant turns keep alternating). The real system prompt — the
Anthropic ``system`` field, or an OpenAI-format leading system message — is left alone.

The hook also recognises Claude Code's compaction request and caps its ``max_tokens`` so the
summary is generated within a known limit (a compaction summary is targeted at ~2000 tokens;
the cap sits above that with headroom so the model is not cut off mid-summary). ``max_tokens``
is a request parameter, not part of the prompt prefix, so capping it does not change the
messages and does not break the llama.cpp prefix cache.
"""

from __future__ import annotations

import os
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

# Cap for a compaction request's output: the summary is targeted at ~2000 tokens, and the
# cap sits above that with headroom so the model is not cut off mid-summary. Only requests
# recognised as compaction are capped; ordinary requests keep whatever max_tokens the
# caller set (or none).
COMPACTION_MAX_TOKENS = 3000
# The fixed marker of the compaction prompt in the last user message (confirmed by the
# binary strings `YTt` / `qSo`). "Additional Instructions:" is NOT used — it appears in
# other contexts as well.
COMPACTION_MARKER = "CRITICAL: Respond with TEXT ONLY"


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    if content is None:
        return []
    return [{"type": "text", "text": str(content)}]


def _text(content: Any) -> str:
    return "\n".join(
        str(b.get("text", "")) for b in _blocks(content) if b.get("type") == "text"
    ).strip()


def move_mid_system_messages(messages: list[Any]) -> list[Any]:
    out: list[Any] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "system" or index == 0:
            out.append(message)
            continue
        text = _text(message.get("content"))
        if not text:
            continue
        reminder = {"type": "text", "text": f"<system-reminder>\n{text}\n</system-reminder>"}
        previous = out[-1] if out else None
        if isinstance(previous, dict) and previous.get("role") == "user":
            out[-1] = {**previous, "content": _blocks(previous.get("content")) + [reminder]}
        else:
            out.append({"role": "user", "content": [reminder]})
    return out


def drop_history_thinking(messages: list[Any]) -> tuple[list[Any], int]:
    """Remove thinking blocks of earlier assistant turns (Anthropic ``thinking`` / ``redacted_thinking`` blocks and
    OpenAI-style ``reasoning_content`` / ``thinking_blocks`` keys). Returns (messages, number of blocks removed).

    Claude Code sends every earlier thinking block back, and the Qwen chat template keeps the reasoning of all
    assistant steps after the last user message, so a long tool loop drags every step's thinking along in the
    context. Without the thinking the next request differs from the cached one only from the previous step's
    thinking on, so the llama.cpp prefix cache still covers everything before it.
    """
    removed = 0
    out: list[Any] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            out.append(message)
            continue
        message = dict(message)
        for key in ("reasoning_content", "thinking_blocks"):
            if message.pop(key, None) is not None:
                removed += 1
        content = message.get("content")
        if isinstance(content, list):
            kept = [b for b in content if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))]
            removed += len(content) - len(kept)
            message["content"] = kept or [{"type": "text", "text": " "}]
        out.append(message)
    return out, removed


# Thinking of local models without a llama-server restart (operator 2026-10-11): the file AHAWR_RUNTIME_DIR/local_thinking holds
# "on" (default) or "off". With "off" every request to a non-claude model carries chat_template_kwargs {"enable_thinking": false}
# (extra_body), so the Qwen template closes the think block and the model answers at once. The file is read on every request
# (cached for 2 s): change it with set_local_thinking.sh, no restart of llama-server or LiteLLM.
RUNTIME_DIR = os.environ.get("AHAWR_RUNTIME_DIR", "/app/runtime")
_thinking_cache: tuple[float, bool] = (0.0, False)


def local_thinking_off(now: float | None = None) -> bool:
    global _thinking_cache
    import time

    t = time.monotonic() if now is None else now
    if t - _thinking_cache[0] < 2.0 and _thinking_cache[0] > 0.0:
        return _thinking_cache[1]
    off = False
    try:
        with open(os.path.join(RUNTIME_DIR, "local_thinking"), encoding="utf-8") as f:
            off = f.read().strip().lower() == "off"
    except OSError:
        off = False
    _thinking_cache = (t if t > 0.0 else 0.001, off)
    return off


def _is_compaction_request(messages: list[Any]) -> bool:
    """True if the last user message starts with the compaction marker after strip().

    Claude Code sends a compaction request as the conversation history plus one final
    user message whose text starts with the fixed marker ``CRITICAL: Respond with
    TEXT ONLY`` (the ``YTt`` part of ``n = YTt + qSo``). Only a prefix match is
    used; a marker quoted anywhere else in the message does not count.
    Mid-conversation ``system`` text is irrelevant here and never used as a signal.
    """
    last_user: Any = None
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            last_user = message
    if last_user is None:
        return False
    return _text(last_user.get("content")).startswith(COMPACTION_MARKER)


class MidSystemMessageHook(CustomLogger):
    async def async_pre_call_hook(  # type: ignore[override]
        self, user_api_key_dict: Any, cache: Any, data: dict[str, Any], call_type: Any
    ) -> dict[str, Any]:
        messages = data.get("messages")
        if isinstance(messages, list):
            data["messages"] = move_mid_system_messages(messages)
            if _is_compaction_request(data["messages"]):
                data["max_tokens"] = COMPACTION_MAX_TOKENS
        # Local Qwen: Claude Code turns its thinking setting into reasoning_effort=high, which the Qwen chat template
        # rejects (it knows xhigh/medium/low only). Drop the setting; the template default and the llama-server
        # reasoning budget (preset) decide how much it thinks.
        if not str(data.get("model", "")).startswith("claude"):
            for key in ("thinking", "reasoning_effort"):
                data.pop(key, None)
            if local_thinking_off():
                extra = data.get("extra_body")
                extra = dict(extra) if isinstance(extra, dict) else {}
                kwargs = dict(extra.get("chat_template_kwargs") or {})
                kwargs["enable_thinking"] = False
                extra["chat_template_kwargs"] = kwargs
                data["extra_body"] = extra
            # Flag AHAWR_DROP_HISTORY_THINKING=1 (default 0): do not carry earlier steps' thinking in the context.
            if os.environ.get("AHAWR_DROP_HISTORY_THINKING", "0") == "1" and isinstance(data.get("messages"), list):
                data["messages"], _ = drop_history_thinking(data["messages"])
            # Flag AHAWR_CACHE_TRACE=1 (default 0): log where this request stops matching the previous one (cache_trace.py).
            if os.environ.get("AHAWR_CACHE_TRACE", "0") == "1" and isinstance(data.get("messages"), list):
                from cache_trace import record

                record(data["messages"], data.get("tools"), str(data.get("model", "")),
                       {"thinking_off": local_thinking_off()})
        return data


proxy_handler_instance = MidSystemMessageHook()
