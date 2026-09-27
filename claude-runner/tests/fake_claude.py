#!/usr/bin/env python3
"""Stand-in for the Claude Code CLI in tests: same flags, same stream-json event shapes.

Directives in the prompt drive the scenario: [[sleep:N]], [[fail:overloaded]],
[[fail:rate_limit]], [[fail:model]], [[max_turns]], [[garbage]], [[crash]].
"""

import json
import os
import re
import sys
import time
from pathlib import Path


def emit(event):
    print(json.dumps(event), flush=True)


def main() -> int:
    args = sys.argv[1:]
    if args == ["--version"]:
        print("9.9.9 (Claude Code fake)")
        return 0
    opts = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"):
            opts[args[i]] = args[i + 1]
            i += 2
        else:
            opts[args[i]] = True
            i += 1
    prompt = sys.stdin.read()
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    project = config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
    sid = opts.get("--resume") or opts.get("--session-id")
    transcript = project / f"{sid}.jsonl"
    log = Path(os.environ["FAKE_CLAUDE_LOG"]) if os.environ.get("FAKE_CLAUDE_LOG") else None
    if log:
        with log.open("a") as fh:
            fh.write(json.dumps({"args": args, "prompt": prompt, "cwd": os.getcwd()}) + "\n")

    if "--resume" in opts and not transcript.exists():
        print(f"No conversation found with session ID: {sid}", file=sys.stderr)
        emit(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "num_turns": 0,
                "session_id": sid,
                "total_cost_usd": 0,
            }
        )
        return 1
    project.mkdir(parents=True, exist_ok=True)
    history = transcript.read_text().splitlines() if transcript.exists() else []
    emit(
        {
            "type": "system",
            "subtype": "init",
            "session_id": sid,
            "cwd": os.getcwd(),
            "model": opts.get("--model", "default"),
        }
    )

    if prompt.strip() == "/compact":
        emit({"type": "system", "subtype": "status", "status": "compacting", "session_id": sid})
        emit(
            {
                "type": "system",
                "subtype": "status",
                "status": None,
                "compact_result": "success",
                "session_id": sid,
            }
        )
        emit(
            {
                "type": "system",
                "subtype": "compact_boundary",
                "session_id": sid,
                "compact_metadata": {
                    "trigger": "manual",
                    "pre_tokens": 150000,
                    "post_tokens": 2000,
                },
            }
        )
        transcript.write_text("\n".join([json.dumps({"compacted": True})]) + "\n")
        emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 0,
                "result": "",
                "session_id": sid,
                "total_cost_usd": 0.01,
            }
        )
        return 0

    # like the real CLI, the prompt is recorded in the transcript before the turn runs
    history.append(json.dumps({"prompt": prompt}))
    transcript.write_text("\n".join(history) + "\n")
    if m := re.search(r"\[\[sleep:(\d+(?:\.\d+)?)\]\]", prompt):
        time.sleep(float(m.group(1)))
    if "[[crash]]" in prompt:
        print("fatal: something broke", file=sys.stderr)
        return 3
    if "[[garbage]]" in prompt:
        print("not json at all", flush=True)

    if "[[fail:overloaded]]" in prompt:
        emit(
            {
                "type": "system",
                "subtype": "api_retry",
                "attempt": 10,
                "max_retries": 10,
                "error_status": 529,
                "error": "overloaded",
                "session_id": sid,
            }
        )
        emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "num_turns": 1,
                "result": "API Error: Repeated 529 Overloaded errors",
                "api_error_status": 529,
                "terminal_reason": "api_error",
                "session_id": sid,
            }
        )
        return 1
    if "[[fail:rate_limit]]" in prompt:
        emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "num_turns": 1,
                "result": "Claude AI usage limit reached|1790000000",
                "session_id": sid,
            }
        )
        return 1
    if "[[fail:model]]" in prompt:
        emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "num_turns": 0,
                "result": "There's an issue with the selected model.",
                "api_error_status": 404,
                "terminal_reason": "api_error",
                "session_id": sid,
            }
        )
        return 1
    if "[[max_turns]]" in prompt:
        emit(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": True,
                "num_turns": 3,
                "session_id": sid,
            }
        )
        return 1

    turns = len(history)
    answer = f"echo[{turns}]: {prompt.strip()[:60]}"
    emit(
        {
            "type": "assistant",
            "parent_tool_use_id": None,
            "session_id": sid,
            "message": {
                "model": opts.get("--model", "default"),
                "content": [{"type": "text", "text": answer}],
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 1000 * turns,
                    "cache_creation_input_tokens": 5,
                },
            },
        }
    )
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "result": answer,
            "session_id": sid,
            "total_cost_usd": 0.001 * turns,
            "permission_denials": [],
            "usage": {"output_tokens": 5},
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
