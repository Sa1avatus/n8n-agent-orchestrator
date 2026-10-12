"""Unit test for the mid-system-message hook (no LiteLLM proxy needed)."""

from __future__ import annotations

import asyncio
import copy
import sys
import types

# The hook only needs CustomLogger as a base class; stub the import so the test
# runs where the full litellm package is unavailable.
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

from ahawr_hooks import MidSystemMessageHook, move_mid_system_messages


def _reminder_text(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict))


def test_leading_system_message_is_left_alone() -> None:
    messages = [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "hi"},
    ]
    out = move_mid_system_messages(messages)
    assert out[0] == {"role": "system", "content": "You are an agent."}
    assert len(out) == 2


def test_mid_system_message_merges_into_preceding_user_message() -> None:
    messages = [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "step one"},
        {"role": "assistant", "content": "ok"},
        {"role": "system", "content": "# Environment\n- dir: /w"},
        {"role": "user", "content": "step two"},
    ]
    out = move_mid_system_messages(messages)
    # the mid system message became a reminder appended to the preceding user message
    reminder_user = [
        m
        for m in out
        if m.get("role") == "user" and "<system-reminder>" in _reminder_text(m["content"])
    ]
    assert len(reminder_user) == 1
    assert "# Environment" in _reminder_text(reminder_user[0]["content"])
    # no mid-conversation system message survives
    assert all(m.get("role") != "system" or m is out[0] for m in out)


def test_mid_system_message_without_preceding_user_becomes_its_own_user_message() -> None:
    messages = [
        {"role": "system", "content": "You are an agent."},
        {"role": "assistant", "content": "ok"},
        {"role": "system", "content": "# Environment"},
    ]
    out = move_mid_system_messages(messages)
    # the reminder is its own user message, so turns keep alternating
    assert out[-1] == {
        "role": "user",
        "content": [{"type": "text", "text": "<system-reminder>\n# Environment\n</system-reminder>"}],
    }


def test_empty_mid_system_message_is_dropped() -> None:
    messages = [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": ""},
    ]
    out = move_mid_system_messages(messages)
    assert len(out) == 2


def test_async_pre_call_hook_moves_mid_system_messages() -> None:
    hook = MidSystemMessageHook()
    data = {
        "messages": [
            {"role": "system", "content": "You are an agent."},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "# Environment"},
        ]
    }
    result = asyncio.run(hook.async_pre_call_hook(None, None, data, "completion"))
    assert result is data
    assert all(
        not isinstance(m, dict) or m.get("role") != "system" or m is result[0] for m in result
    )
    # an ordinary request is not capped
    assert "max_tokens" not in result


def test_compaction_request_is_capped() -> None:
    hook = MidSystemMessageHook()
    data = {
        "messages": [
            {"role": "system", "content": "You are an agent."},
            {"role": "user", "content": "step one"},
            {"role": "assistant", "content": "ok"},
            {
                "role": "user",
                "content": "CRITICAL: Respond with TEXT ONLY. No markdown... "
                "(compaction prompt with 9 sections)",
            },
        ],
        "max_tokens": 8192,
        "system": "You are an agent.",
        "tools": [{"type": "function", "function": {"name": "Bash"}}],
    }
    messages_before = copy.deepcopy(data["messages"])
    system_before = copy.deepcopy(data["system"])
    tools_before = copy.deepcopy(data["tools"])
    result = asyncio.run(hook.async_pre_call_hook(None, None, data, "completion"))
    # max_tokens is capped to the compaction cap
    assert result["max_tokens"] == 3000
    # messages are the same as the original (no mid-system conversion here)
    assert result["messages"] == messages_before
    # system and tools are unchanged
    assert result["system"] == system_before
    assert result["tools"] == tools_before


def test_ordinary_request_with_quoted_marker_mid_text_keeps_max_tokens() -> None:
    # a normal request whose last user message quotes the marker in the middle
    # of the text (not at the start) must NOT be capped
    hook = MidSystemMessageHook()
    data = {
        "messages": [
            {"role": "system", "content": "You are an agent."},
            {"role": "user", "content": "hi"},
            {
                "role": "user",
                "content": (
                    "Task: the docs say 'the marker `CRITICAL: Respond with TEXT ONLY` "
                    "is used in the hook' — quote it here."
                ),
            },
        ],
        "max_tokens": 8192,
        "system": "You are an agent.",
        "tools": [{"type": "function", "function": {"name": "Read"}}],
    }
    result = asyncio.run(hook.async_pre_call_hook(None, None, data, "completion"))
    assert result["max_tokens"] == 8192
    assert result["system"] == "You are an agent."
    assert result["tools"] == [{"type": "function", "function": {"name": "Read"}}]


def test_compaction_request_without_max_tokens_gets_one() -> None:
    hook = MidSystemMessageHook()
    data = {
        "messages": [
            {"role": "user", "content": "CRITICAL: Respond with TEXT ONLY. (compact)"},
        ],
    }
    result = asyncio.run(hook.async_pre_call_hook(None, None, data, "completion"))
    assert result["max_tokens"] == 3000


def test_ordinary_request_keeps_max_tokens() -> None:
    hook = MidSystemMessageHook()
    data = {
        "messages": [
            {"role": "system", "content": "You are an agent."},
            {"role": "user", "content": "hi"},
        ],
        "max_tokens": 8192,
        "system": "You are an agent.",
        "tools": [{"type": "function", "function": {"name": "Read"}}],
    }
    result = asyncio.run(hook.async_pre_call_hook(None, None, data, "completion"))
    # an ordinary request keeps its max_tokens unchanged
    assert result["max_tokens"] == 8192
    assert result["system"] == "You are an agent."
    assert result["tools"] == [{"type": "function", "function": {"name": "Read"}}]


def test_compaction_marker_only_in_last_user_message_counts() -> None:
    # the marker in an earlier user message does not cap; it must be in the last one
    hook = MidSystemMessageHook()
    data = {
        "messages": [
            {"role": "user", "content": "CRITICAL: Respond with TEXT ONLY. (earlier)"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "next step, please"},
        ],
    }
    result = asyncio.run(hook.async_pre_call_hook(None, None, data, "completion"))
    assert "max_tokens" not in result


def test_mid_system_conversion_runs_before_compaction_cap() -> None:
    # a compaction request that also has a mid-conversation system message:
    # the message conversion happens, and max_tokens is still capped.
    hook = MidSystemMessageHook()
    data = {
        "messages": [
            {"role": "system", "content": "You are an agent."},
            {"role": "user", "content": "step one"},
            {"role": "system", "content": "# Environment"},
            {"role": "user", "content": "CRITICAL: Respond with TEXT ONLY. (compact)"},
        ],
        "max_tokens": 8192,
    }
    result = asyncio.run(hook.async_pre_call_hook(None, None, data, "completion"))
    assert result["max_tokens"] == 3000
    # the mid-system message was merged into the preceding user message
    reminder_user = [
        m
        for m in result["messages"]
        if isinstance(m, dict)
        and m.get("role") == "user"
        and "<system-reminder>" in _reminder_text(m.get("content"))
    ]
    assert len(reminder_user) == 1
    assert "# Environment" in _reminder_text(reminder_user[0].get("content"))


def test_drop_history_thinking() -> None:
    from ahawr_hooks import drop_history_thinking

    msgs = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "step one thoughts", "signature": "x"},
            {"type": "text", "text": "plan"},
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "only thoughts"}]},
        {"role": "assistant", "content": "plain", "reasoning_content": "kept thoughts"},
    ]
    out, removed = drop_history_thinking(copy.deepcopy(msgs))
    assert removed == 3
    assert [b["type"] for b in out[1]["content"]] == ["text", "tool_use"]
    assert out[3]["content"] == [{"type": "text", "text": " "}]  # never an empty assistant turn
    assert "reasoning_content" not in out[4] and out[4]["content"] == "plain"
    assert out[0] == msgs[0] and out[2] == msgs[2]
    assert drop_history_thinking([{"role": "user", "content": "x"}]) == ([{"role": "user", "content": "x"}], 0)



def test_local_thinking_flag_file() -> None:
    import os
    import tempfile

    import ahawr_hooks

    with tempfile.TemporaryDirectory() as d:
        ahawr_hooks.RUNTIME_DIR = d
        hook = MidSystemMessageHook()

        def call(model: str) -> dict:
            ahawr_hooks._thinking_cache = (0.0, False)
            return asyncio.run(hook.async_pre_call_hook(None, None, {"model": model, "messages": [{"role": "user", "content": "x"}]}, "completion"))

        # no file: nothing added
        assert "extra_body" not in call("qwen3.8-27b")
        # "on": nothing added
        open(os.path.join(d, "local_thinking"), "w").write("on")
        assert "extra_body" not in call("qwen3.8-27b")
        # "off": local models get enable_thinking=false, merged into an existing extra_body, claude models are untouched
        open(os.path.join(d, "local_thinking"), "w").write(" OFF ")
        out = call("qwen3.8-27b")
        assert out["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
        ahawr_hooks._thinking_cache = (0.0, False)
        data = {"model": "qwen", "messages": [], "extra_body": {"chat_template_kwargs": {"x": 1}, "y": 2}}
        out = asyncio.run(hook.async_pre_call_hook(None, None, data, "completion"))
        assert out["extra_body"] == {"chat_template_kwargs": {"x": 1, "enable_thinking": False}, "y": 2}
        assert "extra_body" not in call("claude-opus-5-5")
        # the flag is cached for 2 s, then re-read
        ahawr_hooks._thinking_cache = (0.0, False)
        open(os.path.join(d, "local_thinking"), "w").write("off")
        assert ahawr_hooks.local_thinking_off(now=100.0) is True
        open(os.path.join(d, "local_thinking"), "w").write("on")
        assert ahawr_hooks.local_thinking_off(now=101.0) is True    # still cached
        assert ahawr_hooks.local_thinking_off(now=103.0) is False   # re-read after 2 s
    ahawr_hooks.RUNTIME_DIR = "/app/runtime"
    ahawr_hooks._thinking_cache = (0.0, False)


if __name__ == "__main__":
    test_leading_system_message_is_left_alone()
    test_mid_system_message_merges_into_preceding_user_message()
    test_mid_system_message_without_preceding_user_becomes_its_own_user_message()
    test_empty_mid_system_message_is_dropped()
    test_async_pre_call_hook_moves_mid_system_messages()
    test_compaction_request_is_capped()
    test_ordinary_request_with_quoted_marker_mid_text_keeps_max_tokens()
    test_compaction_request_without_max_tokens_gets_one()
    test_ordinary_request_keeps_max_tokens()
    test_compaction_marker_only_in_last_user_message_counts()
    test_mid_system_conversion_runs_before_compaction_cap()
    test_drop_history_thinking()
    test_local_thinking_flag_file()
    print("hook unit tests passed")
