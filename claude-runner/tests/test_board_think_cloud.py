"""A cloud (Haiku-style) run and a local (Qwen-style) run with the same think pattern
get the same mode and thinking share, per run and in the per-task statistics.

Only made-up model names, token counts and text. The events go through the real
EventLog, which stamps the step numbers; the step ``think`` flag is then derived the
way think_model_extract.py derives it from the thinking entries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_runner.board_think import _mode_bucket, _think_share, variant_stats
from claude_runner.events import EventLog

THINK_TEXT = [
    {"type": "thinking", "thinking": "plan the edit"},
    {"type": "text", "text": "editing"},
]
TEXT = [{"type": "text", "text": "done"}]
REDACTED = [
    {"type": "redacted_thinking", "data": "opaque"},
    {"type": "text", "text": "checking"},
]


def _message(
    model: str, blocks: list[dict[str, Any]], input_tokens: int, output_tokens: int
) -> list[dict[str, Any]]:
    usage = {"input_tokens": input_tokens, "cache_read_input_tokens": 0}
    return [
        {
            "type": "stream_event",
            "event": {
                "type": "message_start",
                "message": {"model": model, "usage": usage},
            },
        },
        {"type": "assistant", "message": {"model": model, "content": blocks}},
        {
            "type": "stream_event",
            "event": {"type": "message_delta", "usage": {"output_tokens": output_tokens}},
        },
        {"type": "stream_event", "event": {"type": "message_stop"}},
    ]


def _pattern(model: str, tokens: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """Four requests: thinking, plain, redacted thinking, plain — two of four think."""
    blocks = [THINK_TEXT, TEXT, REDACTED, TEXT]
    events: list[dict[str, Any]] = []
    for content, (input_tokens, output_tokens) in zip(blocks, tokens, strict=True):
        events += _message(model, content, input_tokens, output_tokens)
    return events


CLOUD_EVENTS = _pattern(
    "claude-haiku-made-up", [(1200, 300), (1400, 80), (1500, 260), (1600, 70)]
)
LOCAL_EVENTS = _pattern(
    "qwen-made-up-local", [(800, 150), (900, 40), (950, 210), (1000, 35)]
)


def _steps(log: EventLog, run_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    log.open(run_id)
    for event in events:
        log.feed(run_id, event)
    entries, _ = log.read(run_id)
    log.close(run_id)
    thinks = {entry["step"] for entry in entries if entry["kind"] == "thinking"}
    return [
        {
            "think": entry["step"] in thinks,
            **{k: v for k, v in entry.items() if k.endswith("_tokens")},
        }
        for entry in entries
        if entry["kind"] == "step"
    ]


def test_cloud_and_local_runs_get_the_same_mode_and_share(tmp_path: Path) -> None:
    log = EventLog(tmp_path, retention_days=0)
    cloud_steps = _steps(log, "cloud", CLOUD_EVENTS)
    local_steps = _steps(log, "local", LOCAL_EVENTS)

    assert len(cloud_steps) == len(local_steps) == 4
    assert [step["think"] for step in cloud_steps] == [True, False, True, False]
    assert [step["think"] for step in local_steps] == [True, False, True, False]
    assert _think_share(cloud_steps) == _think_share(local_steps) == 0.5
    assert _mode_bucket(_think_share(cloud_steps)) == "think"
    assert _mode_bucket(_think_share(local_steps)) == "think"


def test_per_task_statistics_count_both_runs_alike(tmp_path: Path) -> None:
    log = EventLog(tmp_path, retention_days=0)
    cloud_steps = _steps(log, "cloud", CLOUD_EVENTS)
    local_steps = _steps(log, "local", LOCAL_EVENTS)
    attempts = [
        {"variant": "cloud", "pass": True, "secs": 120, "steps": cloud_steps,
         "mission": "M", "task": "T001"},
        {"variant": "local", "pass": True, "secs": 150, "steps": local_steps,
         "mission": "M", "task": "T001"},
    ]
    stats = {row["variant"]: row for row in variant_stats(attempts, control="local")}

    assert stats["cloud"]["share_with_thinking"] == stats["local"]["share_with_thinking"] == 1.0
    assert (
        stats["cloud"]["share_without_thinking"]
        == stats["local"]["share_without_thinking"]
        == 0.0
    )
