"""Claude Code CLI invocation (``claude -p --output-format stream-json``) and stream parsing."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import CREDENTIAL_VARS, ProviderProfile, RoleProfile, Settings

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


def _compact_hook_command(instructions: str) -> str:
    return f"cat <<'AHAWR_EOF'\n{instructions}\nAHAWR_EOF"


def _compact_settings_path(instructions: str) -> str:
    """A settings file carrying a PreCompact hook that prints ``instructions`` to stdout.

    Claude Code appends the hook's stdout to the compaction request as custom compact
    instructions; the hook's matcher covers both ``auto`` and ``manual`` compaction.
    """
    path = config_dir() / "compact-settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "hooks": {
            "PreCompact": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": _compact_hook_command(instructions),
                        }
                    ]
                }
            ]
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _search_first_settings_path(every: int, compact_instructions: str) -> str:
    """A settings file with the search-first PreToolUse hook (search_hook.py) for Bash, Grep and
    Glob, plus the PreCompact hook when ``compact_instructions`` is given."""
    path = config_dir() / "search-first-settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    search = shlex.quote(sys.executable) + " -m claude_runner.search_hook"
    hooks: dict[str, Any] = {
        "PreToolUse": [
            {
                "matcher": "Bash|Grep|Glob",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"AHAWR_SEARCH_FIRST_EVERY={every} {search}",
                        "timeout": 10,
                    }
                ],
            }
        ]
    }
    if compact_instructions:
        hooks["PreCompact"] = [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": _compact_hook_command(compact_instructions),
                    }
                ]
            }
        ]
    path.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    return str(path)


def build_command(
    settings: Settings,
    profile: RoleProfile,
    *,
    model: str,
    claude_session_id: str,
    resume: bool,
    compact: bool = False,
    provider: ProviderProfile | None = None,
    search_first: bool = False,
) -> list[str]:
    cmd = [settings.claude_bin, "-p", "--output-format", "stream-json", "--verbose"]
    if settings.live_tokens and not compact:
        # token deltas for the dashboard's live view of the block being generated
        cmd.append("--include-partial-messages")
    if model:
        cmd += ["--model", model]
    cmd += ["--resume" if resume else "--session-id", claude_session_id]
    if settings.bare:
        cmd.append("--bare")
    if not compact:
        cmd += ["--permission-mode", profile.permission_mode]
        for folder in profile.add_dirs:
            cmd += ["--add-dir", folder]
        if profile.allowed_tools:
            cmd += ["--allowedTools", ",".join(profile.allowed_tools)]
        if profile.disallowed_tools:
            cmd += ["--disallowedTools", ",".join(profile.disallowed_tools)]
        tools = settings.tools_for(profile, provider)
        if tools:
            # A smaller tool set shrinks the system prompt (~15k → ~4k tokens), which matters
            # for local models with small context windows.
            cmd += ["--tools", tools]
        if profile.append_system_prompt:
            cmd += ["--append-system-prompt", profile.append_system_prompt]
        max_turns = profile.max_turns or settings.max_turns
        if max_turns:
            cmd += ["--max-turns", str(max_turns)]
        if settings.max_budget_usd > 0:
            cmd += ["--max-budget-usd", f"{settings.max_budget_usd:g}"]
    # The compact-instructions settings file is added only for the local provider, where
    # the small context makes the structured summary matter. Other providers keep the
    # stock summarizer and their prompt cache: the hook only fires on compaction, its text
    # is appended to the *end* of the compaction request, and the same file is used for
    # every run, so system prompt and tools stay unchanged step to step. Empty instructions
    # (the built-in summarizer) add no file at all.
    compact_hook = bool(
        settings.local_compact_instructions
        and provider is not None
        and provider.isolates_credentials
        and settings.compact_instructions
    )
    if search_first and not compact:
        instructions = settings.compact_instructions if compact_hook else ""
        cmd += [
            "--settings",
            _search_first_settings_path(settings.search_first_every, instructions),
        ]
    elif compact_hook:
        cmd += ["--settings", _compact_settings_path(settings.compact_instructions)]
    cmd += list(settings.extra_args)
    return cmd


def settings_flag(cmd: list[str]) -> str | None:
    """The path of the ``--settings`` file on a command, if any."""
    if "--settings" in cmd:
        return cmd[cmd.index("--settings") + 1]
    return None


# Model names Claude Code uses besides --model: aliases, background tasks, subagents.
SECONDARY_MODEL_VARS = (
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)


def child_env(provider: ProviderProfile | None = None, model: str = "") -> dict[str, str]:
    """The CLI inherits the container env (model credentials) minus the runner's own settings,
    with the run's provider block on top. A provider with its own endpoint or credential does
    not inherit the container's credentials, and all of Claude Code's secondary model names
    default to the run's model, so hermes_config *_model alone decides the model."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_RUNNER_")}
    if provider is not None:
        if provider.isolates_credentials:
            for key in CREDENTIAL_VARS:
                env.pop(key, None)
            for key in SECONDARY_MODEL_VARS:
                env.pop(key, None)
                if model and key not in provider.env:
                    env[key] = model
        env.update(provider.env)
    env.setdefault("DISABLE_AUTOUPDATER", "1")
    env.setdefault("CLAUDE_CODE_RESUME_INTERRUPTED_TURN", "1")
    return env


# a reply cut at the output limit counts as part of the final report when it is one of the
# last main-thread replies (the model may read its file back once before resuming)
CUT_REPLY_WINDOW = 4


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
    # main-thread replies in order (message id -> text) and the ids cut at the output limit
    replies: dict[str, str] = field(default_factory=dict)
    cut: list[str] = field(default_factory=list)

    def feed(self, line: str) -> dict[str, Any] | None:
        """Record one stream line; returns the parsed event (None for noise)."""
        line = line.strip()
        if not line:
            return None
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            self.bad_lines += 1
            return None
        if not isinstance(event, dict):
            return None
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
                message_id = str(message.get("id") or "")
                if message_id:
                    self.replies[message_id] = self.replies.get(message_id, "") + "".join(texts)
                    if message.get("stop_reason") == "max_tokens":
                        self._mark_cut(message_id)
                size = _prompt_tokens(message.get("usage") or {})
                if size:  # gateways that stream usage only at the end report 0 here
                    self.context_tokens = size
            self.model = str(message.get("model") or self.model)
        elif kind == "stream_event" and event.get("parent_tool_use_id") is None:
            # LiteLLM reports a request's size only in its stream events; this is also the
            # only size a run that timed out (no result) leaves behind
            inner = event.get("event") or {}
            if (inner.get("delta") or {}).get("stop_reason") == "max_tokens" and self.replies:
                # with partial messages the stop reason arrives after the reply's blocks
                self._mark_cut(next(reversed(self.replies)))
            if inner.get("type") in ("message_start", "message_delta"):
                usage = inner.get("usage") or (inner.get("message") or {}).get("usage") or {}
                size = _prompt_tokens(usage)
                if size:
                    self.context_tokens = size
        elif kind == "system" and subtype == "api_retry":
            self.last_retry = event
        elif kind == "system" and subtype == "compact_boundary":
            self.compact = dict(event.get("compact_metadata") or {})
            post = self.compact.get("post_tokens")
            if isinstance(post, int):
                self.context_tokens = post
        elif kind == "system" and subtype == "status" and event.get("compact_result"):
            self.compact_result = str(event.get("compact_result"))
        return event

    def _mark_cut(self, message_id: str) -> None:
        if message_id not in self.cut:
            self.cut.append(message_id)

    def full_result(self, text: str) -> str:
        """The run's result with the parts of a reply that hit the output-token limit.

        Claude Code then asks the model to resume, and `result` holds only the
        continuation. Cut replies among the last few are put back in front of it; an
        earlier cut belongs to the work, not to the final report."""
        recent = list(self.replies)[-CUT_REPLY_WINDOW:]
        parts = [
            self.replies[i].strip()
            for i in recent
            if i in self.cut and self.replies[i].strip() and self.replies[i].strip() != text.strip()
        ]
        return "\n\n".join(parts + [text]) if parts else text

    def final_context_tokens(self) -> int | None:
        """Context size after the run: the last main-thread request, or — when a gateway
        (e.g. LiteLLM in front of llama.cpp) reports no per-request usage — the average
        prompt size per turn from the run totals."""
        if self.context_tokens or self.compact is not None:
            return self.context_tokens
        result = self.result or {}
        total = _prompt_tokens(result.get("usage") or {})
        if not total:
            return self.context_tokens
        return total // max(int(result.get("num_turns") or 1), 1)


def _prompt_tokens(usage: dict[str, Any]) -> int:
    return sum(
        int(usage.get(key) or 0)
        for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    )


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
        "duration_api_ms": result.get("duration_api_ms"),
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
        return Outcome(
            "completed", state.full_result(str(result.get("result") or "")), details=details
        )

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
