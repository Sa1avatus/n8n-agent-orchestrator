"""LiteLLM proxy hooks for claude-runner → local OpenAI-compatible models.

Claude Code sends some context (e.g. the "# Environment" block) as ``role: "system"`` messages
in the middle of ``messages``. Many local chat templates (Qwen, Llama, Gemma ...) accept a
system message only at the very beginning and fail with e.g. "System message must be at the
beginning". This hook turns every such mid-conversation system message into a
``<system-reminder>`` text block of a user message (merged into the preceding user message when
there is one, so user/assistant turns keep alternating). The real system prompt — the
Anthropic ``system`` field, or an OpenAI-format leading system message — is left alone.
"""

from __future__ import annotations

from typing import Any

from litellm.integrations.custom_logger import CustomLogger


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


class MidSystemMessageHook(CustomLogger):
    async def async_pre_call_hook(  # type: ignore[override]
        self, user_api_key_dict: Any, cache: Any, data: dict[str, Any], call_type: Any
    ) -> dict[str, Any]:
        messages = data.get("messages")
        if isinstance(messages, list):
            data["messages"] = move_mid_system_messages(messages)
        return data


proxy_handler_instance = MidSystemMessageHook()
