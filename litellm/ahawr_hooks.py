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
        return data


proxy_handler_instance = MidSystemMessageHook()
