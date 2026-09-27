"""Claude Code CLI invocation (``claude -p --output-format stream-json``) and stream parsing."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import RoleProfile, Settings

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
SESSION_MISSING_RE = re.compile(r"no conversation found", re.IGNORECASE)
LIMIT_RE = re.compile(r"usage limit|rate limit|hit your limit|limit reached", re.IGNORECASE)
OVERLOAD_RE = re.compile(r"overloaded", re.IGNORECASE)

# Error categories of `system/api_retry` events → HTTP code AHAWR's retry logic understands.
RETRY_HTTP = {"rate_limit": 429, "overloaded": 529, "server_error": 500}
TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504, 529}


def config_dir() -> Path:
    raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(raw) if raw else Path.home() / ".claude"


def transcript_exists(claude_session_id: str) -> bool:
    """Claude Code keeps each session at ``<config>/projects/<project>/<id>.jsonl``."""
    if not UUID_RE.match(claude_session_id):
        return False
    projects = config_dir() / "projects"
    return projects.is_dir() and any(projects.glob(f"*/{claude_session_id}.jsonl"))


def build_command(
    settings: Settings,
    profile: RoleProfile,
    *,
    model: str,
    claude_session_id: str,
    resume: bool,
    compact: bool = False,
) -> list[str]:
    cmd = [settings.claude_bin, "-p", "--output-format", "stream-json", "--verbose"]
    if model:
        cmd += ["--model", model]
    cmd += ["--resume" if resume else "--session-id", claude_session_id]
    if settings.bare:
        cmd.append("--bare")
    if not compact:
        cmd += ["--permission-mode", profile.permission_mode]
        if profile.allowed_tools:
            cmd += ["--allowedTools", ",".join(profile.allowed_tools)]
        if profile.disallowed_tools:
            cmd += ["--disallowedTools", ",".join(profile.disallowed_tools)]
        if profile.append_system_prompt:
            cmd += ["--append-system-prompt", profile.append_system_prompt]
        max_turns = profile.max_turns or settings.max_turns
        if max_turns:
            cmd += ["--max-turns", str(max_turns)]
        if settings.max_budget_usd > 0:
            cmd += ["--max-budget-usd", f"{settings.max_budget_usd:g}"]
    cmd += list(settings.extra_args)
    return cmd


def child_env() -> dict[str, str]:
    """The CLI inherits the container env (model credentials) minus the runner's own secrets."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_RUNNER_")}
    env.setdefault("DISABLE_AUTOUPDATER", "1")
    env.setdefault("CLAUDE_CODE_RESUME_INTERRUPTED_TURN", "1")
    return env


@dataclass
class StreamState:
    """Everything the runner keeps from one CLI stream."""

    session_id: str = ""
    result: dict[str, Any] | None = None
    last_text: str = ""
    context_tokens: int | None = None
    last_retry: dict[str, Any] | None = None
    compact: dict[str, Any] | None = None
    compact_result: str = ""
    model: str = ""
    events: int = 0
    bad_lines: int = 0
    permission_denials: list[Any] = field(default_factory=list)

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            self.bad_lines += 1
            return
        if not isinstance(event, dict):
            return
        self.events += 1
        self.session_id = str(event.get("session_id") or self.session_id)
        kind, subtype = event.get("type"), event.get("subtype")
        if kind == "result":
            self.result = event
            self.permission_denials = list(event.get("permission_denials") or [])
        elif kind == "assistant":
            message = event.get("message") or {}
            if event.get("parent_tool_use_id") is None:
                texts = [
                    str(block.get("text", ""))
                    for block in message.get("content") or []
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                if any(texts):
                    self.last_text = "".join(texts)
                usage = message.get("usage") or {}
                if usage:
                    self.context_tokens = sum(
                        int(usage.get(key) or 0)
                        for key in (
                            "input_tokens",
                            "cache_read_input_tokens",
                            "cache_creation_input_tokens",
                        )
                    )
            self.model = str(message.get("model") or self.model)
        elif kind == "system" and subtype == "api_retry":
            self.last_retry = event
        elif kind == "system" and subtype == "compact_boundary":
            self.compact = dict(event.get("compact_metadata") or {})
            post = self.compact.get("post_tokens")
            if isinstance(post, int):
                self.context_tokens = post
        elif kind == "system" and subtype == "status" and event.get("compact_result"):
            self.compact_result = str(event.get("compact_result"))


@dataclass
class Outcome:
    status: str  # completed | failed | cancelled
    output: str = ""
    error_code: str = ""
    error_message: str = ""
    http_code: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


def classify(
    state: StreamState,
    returncode: int | None,
    stderr: str,
    *,
    timed_out: bool = False,
    cancelled: bool = False,
    timeout_seconds: int = 0,
) -> Outcome:
    """Map a finished CLI process to the Hermes run contract used by the Run Manager."""
    result = state.result or {}
    details: dict[str, Any] = {
        "exit_code": returncode,
        "subtype": result.get("subtype"),
        "terminal_reason": result.get("terminal_reason"),
        "num_turns": result.get("num_turns"),
        "total_cost_usd": result.get("total_cost_usd"),
        "usage": result.get("usage"),
        "permission_denials": state.permission_denials,
        "model": state.model,
    }
    if state.compact is not None:
        details["compact"] = state.compact
    partial = str(result.get("result") or state.last_text or "")
    if cancelled:
        return Outcome("cancelled", partial, "cancelled", "Run was cancelled.", None, details)
    if timed_out:
        message = f"Claude Code run timed out after {timeout_seconds} s."
        return Outcome("failed", partial, "timeout", message, 408, details)
    if result and not result.get("is_error") and result.get("subtype") == "success":
        return Outcome("completed", str(result.get("result") or ""), details=details)

    text = str(result.get("result") or "").strip()
    subtype = str(result.get("subtype") or "")
    api_status = result.get("api_error_status")
    api_status = int(api_status) if isinstance(api_status, int) else None
    details["api_error_status"] = api_status
    # `http_code` is what the Run Manager reads as *the runner's* status: 404 there means
    # "run not found". Only transient upstream statuses are passed through; a 404 for an
    # unknown model, for example, stays in details.api_error_status.
    http_code: int | None = None
    retry = state.last_retry or {}
    code = ""
    if subtype == "error_max_turns":
        code, text = "max_turns", text or "Claude Code stopped at the max-turns limit."
    elif subtype == "error_max_budget_usd":
        code, text = "max_budget", text or "Claude Code stopped at the max-budget limit."
    elif SESSION_MISSING_RE.search(stderr) or SESSION_MISSING_RE.search(text):
        code = "session_not_found"
        text = text or stderr.strip().splitlines()[-1]
    elif (
        retry.get("error") in RETRY_HTTP
        or api_status in TRANSIENT_HTTP
        or LIMIT_RE.search(text)
        or OVERLOAD_RE.search(text)
    ):
        category = str(retry.get("error") or "")
        if LIMIT_RE.search(text) or category == "rate_limit" or api_status == 429:
            code = "rate_limit"
        elif OVERLOAD_RE.search(text) or category == "overloaded" or api_status == 529:
            code = "overloaded"
        else:
            code = "server_error"
        retry_status = retry.get("error_status")
        http_code = (
            api_status
            if api_status in TRANSIENT_HTTP
            else retry_status
            if retry_status in TRANSIENT_HTTP
            else RETRY_HTTP[code]
        )
    elif api_status:
        code = str(result.get("terminal_reason") or "api_error")
    elif not result:
        code = "cli_error"
    else:
        code = subtype or "execution_error"
    if not text:
        tail = stderr.strip().splitlines()[-3:]
        text = " ".join(tail) or f"Claude Code exited with code {returncode}."
    # AHAWR's retry logic recognises transient failures by HTTP code and wording.
    if code == "rate_limit" and "rate limit" not in text.lower():
        text += " (rate limit)"
    if code == "overloaded" and "overloaded" not in text.lower():
        text += " (overloaded)"
    return Outcome("failed", partial, code, text, http_code, details)
