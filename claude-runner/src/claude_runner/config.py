"""Runner settings from ``CLAUDE_RUNNER_*`` environment variables.

Role profiles decide what Claude Code may do for each AHAWR role. The defaults keep the
Architect and the Reviewer read-only and let the Worker edit and run commands inside the
mounted workspace, while never reading ``.env`` files or committing/pushing.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

ROLES = ("architect", "worker", "reviewer", "qa", "generic")
PERMISSION_MODES = {
    "default",
    "acceptEdits",
    "plan",
    "auto",
    "dontAsk",
    "bypassPermissions",
    "manual",
}
COMPACT_MODES = {"auto", "always", "off"}

# Compact-instruction text (delivered to Claude Code as custom compact instructions via the
# PreCompact hook and the runner's /compact). It fixes the required summary sections, the
# target volume (1500-2000 tokens, hard cap 2000) and forbids verbatim copying of tool
# outputs, logs, diffs and code; it is short so it does not add to the compaction request's
# prefill.
DEFAULT_COMPACT_INSTRUCTIONS = """
Compress the session into a short structured summary of 1500-2000 tokens (hard cap 2000).
Do not copy tool outputs, logs, diffs, or code verbatim; use file:line references instead.

## Task and acceptance criteria
State the original task and its acceptance criteria verbatim.

## Findings (file:line)
List the key facts found, each with a file:line reference.

## Changed files
List the files that were changed.

## Checks done / not done
List each check with its result; separate done from not done.

## Next step
State the next step.
"""


_READ_ONLY_DENY = ["Edit", "Write", "NotebookEdit"]
# Env files with real values. Named instead of `.env.*`, which would also hide `.env.example`
# (Claude Code applies deny rules before allow rules, so it cannot be re-allowed).
_SECRET_ENV_FILES = [
    ".env",
    ".env.local",
    ".env.*.local",
    ".env.development",
    ".env.dev",
    ".env.production",
    ".env.prod",
    ".env.staging",
    ".env.test",
]
_SECRET_DENY = [f"Read({where}{name})" for where in ("./", "**/") for name in _SECRET_ENV_FILES]
# A Worker that compares files with HEAD wiped the uncommitted edits of the whole tree three times
# (a stash it then dropped, `git show HEAD:<file> > <file>`): git commands that change the tree
# or the stash stay denied, with and without `-C <dir>`, and so does writing `git show` output
# to a file.
_GIT_TREE_CMDS = ("stash", "checkout", "restore", "reset", "clean", "switch", "rebase", "apply")
_GIT_DENY = [
    "Bash(git commit *)",
    "Bash(git push *)",
    *(f"Bash(git {name})" for name in _GIT_TREE_CMDS),
    *(f"Bash(git {name} *)" for name in _GIT_TREE_CMDS),
    *(f"Bash(git -C * {name} *)" for name in _GIT_TREE_CMDS),
    "Bash(git show *>*)",
    "Bash(git -C * show *>*)",
]
# The Reviewer runs in `dontAsk`, where a Bash command works only if a rule allows it (a real
# run: 105 denied Bash calls, among them plain grep, wc, git status and ahawr-search). It gets
# read-only inspection commands and the retrieval CLI, so it can check a Worker's claims itself.
# Commands that write, build or change git state stay denied. Redirections to a file are
# denied too (an allow rule for `head *` would otherwise also match `head x > y`); this is
# defence in depth for an Opus Reviewer, not a sandbox.
_REVIEWER_READ_CMDS = [
    "ahawr-search",
    "grep",
    "ls",
    "wc",
    "head",
    "tail",
    "cat",
    "md5sum",
    "sha256sum",
    "stat",
    "file",
    "diff",
    "cmp",
    "sort",
    "uniq",
    "cut",
    "pwd",
    "cd",
    "bash -n",
]
_REVIEWER_GIT_READ = [
    "diff",
    "status",
    "log",
    "show",
    "rev-parse",
    "ls-files",
    "ls-tree",
    "cat-file",
    "blame",
    "grep",
    "describe",
]
_REVIEWER_BASH_ALLOW = (
    [f"Bash({c} *)" for c in _REVIEWER_READ_CMDS]
    + ["Bash(pwd)"]
    + [f"Bash(git {g} *)" for g in _REVIEWER_GIT_READ]
    + [f"Bash(git -C * {g} *)" for g in _REVIEWER_GIT_READ]
    + [f"Bash(git {g})" for g in _REVIEWER_GIT_READ]
)
_REVIEWER_BASH_DENY = ["Bash(* >*)", "Bash(git * --output*)", "Bash(*.env*)"]


@dataclass(frozen=True)
class RoleProfile:
    permission_mode: str
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    append_system_prompt: str = ""
    max_turns: int = 0
    # Built-in tool set offered to the model (`--tools`) for this role only; empty = inherit.
    tools: str = ""
    # Extra folders the role may use besides the run's working directory (`--add-dir`), e.g.
    # a Worker's scratch folder the read-only Reviewer must inspect.
    add_dirs: list[str] = field(default_factory=list)


# Model-access variables. A provider that sets its own endpoint gets only its own credentials,
# so e.g. a local Worker never sees the Anthropic subscription token.
CREDENTIAL_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
)
PROVIDER_PREFIX = "CLAUDE_RUNNER_PROVIDER_"
# Keys of a provider block that configure the runner instead of the Claude Code process.
# CONTEXT_PROBE_URL / CONTEXT_PROBE_KEY: read the model's real window from llama-server before
# each run (context_probe.py); the key never reaches the Claude Code process. COMPACT_PCT: the
# autocompact trigger as a percentage of that window. COMPACT_MIN_PCT: the runner's /compact
# before resuming a session, as a percentage of that window (default: COMPACT_MIN_TOKENS' share
# of 65536). SEARCH_FIRST: steer runs of this provider to ``ahawr-search`` (search_hook.py); empty
# = the CLAUDE_RUNNER_SEARCH_FIRST default. RETRY_FRESH_SESSION: a retry with a previous session
# starts a fresh session (no pre-retry /compact) seeded with the four-part retry input; the
# input is cut to RETRY_INPUT_CHARS characters.
PROVIDER_RUNNER_KEYS = {
    "TOOLS",
    "COMPACT_MIN_TOKENS",
    "COMPACT_MIN_PCT",
    "CONTEXT_PROBE_URL",
    "CONTEXT_PROBE_KEY",
    "COMPACT_PCT",
    "SEARCH_FIRST",
    "RETRY_FRESH_SESSION",
}

# Character budget for the fresh-session retry input (task, review findings, previous report,
# previous session's digest, newest last). The digest fills most of it; it is built at roughly
# budget - 4000, leaving room for the three other parts.
DEFAULT_RETRY_INPUT_CHARS = 32_000

# Context-window limits the local provider's compaction settings must stay inside. The local
# model's window is 65536 tokens; the autocompact threshold must leave room for one large
# tool output (~20K tokens, saved to a file by BASH_MAX_OUTPUT_LENGTH and similar limits)
# plus the generation limit, or a run could exceed the window before the next compaction.
AUTO_COMPACT_WINDOW = 65_536
AUTO_COMPACT_TOOL_RESERVE = 20_000
DEFAULT_MAX_OUTPUT_TOKENS = 8_192
QA_MAX_MINUTES = 20
PROVIDERS_RELOAD_POLL_SECONDS = 30


@dataclass(frozen=True)
class ProviderProfile:
    """A named model backend, selected per run by the request's ``provider`` (per AHAWR role,
    like Hermes' ``*_provider``). ``env`` is overlaid on the Claude Code process environment."""

    name: str
    env: dict[str, str] = field(default_factory=dict)
    tools: str = ""
    compact_min_tokens: int | None = None
    # Autocompact threshold in tokens (from CLAUDE_AUTOCOMPACT_PCT_OVERRIDE or
    # CLAUDE_CODE_AUTO_COMPACT_WINDOW); None = the provider's built-in default.
    autocompact_threshold: int | None = None
    # Max tokens per response (CLAUDE_CODE_MAX_OUTPUT_TOKENS); None = the provider's default.
    max_output_tokens: int | None = None
    # llama-server to ask for the model's --ctx-size before each run; "" = static settings only
    context_probe_url: str = ""
    context_probe_key: str = field(default="", repr=False)
    # autocompact trigger as a percentage of the probed window; None = derived (context_probe)
    compact_pct: float | None = None
    # pre-resume /compact threshold as a percentage of the probed window; None = derived
    compact_min_pct: float | None = None
    # steer runs to ahawr-search (search_hook.py); None = the global CLAUDE_RUNNER_SEARCH_FIRST
    search_first: bool | None = None
    # retries with a previous session start a fresh one (no pre-retry /compact), seeded with
    # the four-part retry input (task, review findings, previous report, digest); None = the
    # global CLAUDE_RUNNER_RETRY_FRESH_SESSION
    retry_fresh_session: bool | None = None

    @property
    def isolates_credentials(self) -> bool:
        return any(key in self.env for key in CREDENTIAL_VARS)


def provider_key(name: str) -> str:
    """``custom:llama-local`` / ``llama-local`` → ``CUSTOM_LLAMA_LOCAL`` / ``LLAMA_LOCAL``."""
    return re.sub(r"[^A-Z0-9]+", "_", name.strip().upper()).strip("_")


# The precompute buffer the binary subtracts from the effective window before
# applying the threshold (hardcoded in the Claude Code bundle).
AUTO_COMPACT_PRECOMPUTE_BUFFER = 13_000


def _autocompact_threshold(block: dict[str, str]) -> int | None:
    """The provider's autocompact trigger in tokens.

    ``CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`` is a percentage of the 65536-token window
    (e.g. 56.98 → 37342).  ``CLAUDE_CODE_AUTO_COMPACT_WINDOW`` is the resolved
    window; the actual trigger is
    ``window - min(max_output, 20000) - 13000``.
    None when neither is set.
    """
    pct = block.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "").strip()
    if pct:
        return int(float(pct) * AUTO_COMPACT_WINDOW / 100)
    window = block.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "").strip()
    if window:
        w = int(window)
        max_output = _max_output_tokens(block) or DEFAULT_MAX_OUTPUT_TOKENS
        return w - min(max_output, AUTO_COMPACT_TOOL_RESERVE) - AUTO_COMPACT_PRECOMPUTE_BUFFER
    return None


def _pct(name: str, raw: str, key: str = "COMPACT_PCT") -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    value = float(raw)
    if not 0 < value <= 100:
        raise ValueError(f"provider {name}: {key} must be in (0, 100], got {raw}")
    return value


def _opt_bool(name: str, raw: str, key: str) -> bool | None:
    raw = raw.strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"provider {name}: {key} must be true or false, got {raw!r}")


def _max_output_tokens(block: dict[str, str]) -> int | None:
    raw = block.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "").strip()
    return int(raw) if raw else None


def _validate_compaction_limits(name: str, block: dict[str, str]) -> None:
    """The autocompact trigger must stay inside the provider's window and leave
    headroom for one large tool output.  The headroom is ``AUTO_COMPACT_WINDOW -
    threshold``; it must cover ``AUTO_COMPACT_TOOL_RESERVE`` (one ~20K tool
    output).  For the window path the trigger is
    ``window - min(max_output, 20000) - 13000``, so the headroom is exactly
    ``min(max_output, 20000) + 13000``."""
    threshold = _autocompact_threshold(block)
    if threshold is None:
        return
    if threshold > AUTO_COMPACT_WINDOW:
        raise ValueError(
            f"provider {name}: autocompact threshold {threshold} exceeds "
            f"the {AUTO_COMPACT_WINDOW}-token window"
        )
    headroom = AUTO_COMPACT_WINDOW - threshold
    if headroom < AUTO_COMPACT_TOOL_RESERVE:
        raise ValueError(
            f"provider {name}: autocompact threshold {threshold} leaves only "
            f"{headroom} tokens of headroom, less than the "
            f"{AUTO_COMPACT_TOOL_RESERVE}-token tool reserve"
        )


def _providers(env: dict[str, str]) -> dict[str, ProviderProfile]:
    """``CLAUDE_RUNNER_PROVIDER_<NAME>__<VAR>=value`` (double underscore) → providers."""
    blocks: dict[str, dict[str, str]] = {}
    for key, value in env.items():
        if not key.startswith(PROVIDER_PREFIX) or "__" not in key:
            continue
        name, var = key[len(PROVIDER_PREFIX) :].split("__", 1)
        if name and var:
            blocks.setdefault(provider_key(name), {})[var] = value
    providers = {}
    for name, block in blocks.items():
        compact = block.get("COMPACT_MIN_TOKENS", "").strip()
        _validate_compaction_limits(name, block)
        providers[name] = ProviderProfile(
            name=name,
            env={k: v for k, v in block.items() if k not in PROVIDER_RUNNER_KEYS},
            tools=block.get("TOOLS", "").strip(),
            compact_min_tokens=int(compact) if compact else None,
            autocompact_threshold=_autocompact_threshold(block),
            max_output_tokens=_max_output_tokens(block),
            context_probe_url=block.get("CONTEXT_PROBE_URL", "").strip(),
            context_probe_key=block.get("CONTEXT_PROBE_KEY", "").strip(),
            compact_pct=_pct(name, block.get("COMPACT_PCT", "")),
            compact_min_pct=_pct(name, block.get("COMPACT_MIN_PCT", ""), "COMPACT_MIN_PCT"),
            search_first=_opt_bool(name, block.get("SEARCH_FIRST", ""), "SEARCH_FIRST"),
            retry_fresh_session=_opt_bool(
                name, block.get("RETRY_FRESH_SESSION", ""), "RETRY_FRESH_SESSION"
            ),
        )
    return providers


DEFAULT_PROFILES: dict[str, RoleProfile] = {
    "architect": RoleProfile("dontAsk", disallowed_tools=_READ_ONLY_DENY + _SECRET_DENY),
    "worker": RoleProfile("bypassPermissions", disallowed_tools=_SECRET_DENY + _GIT_DENY),
    "reviewer": RoleProfile(
        "dontAsk",
        allowed_tools=_REVIEWER_BASH_ALLOW,
        disallowed_tools=_READ_ONLY_DENY + _SECRET_DENY + _REVIEWER_BASH_DENY,
    ),
    "qa": RoleProfile("dontAsk", disallowed_tools=_READ_ONLY_DENY + _SECRET_DENY),
    "generic": RoleProfile("dontAsk", disallowed_tools=_READ_ONLY_DENY + _SECRET_DENY),
}


def _list(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def _int(env: dict[str, str], name: str, default: int, minimum: int = 0) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _float(env: dict[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    return float(raw) if raw else default


def _bool(env: dict[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _path_map(raw: str) -> list[tuple[str, str]]:
    """``D:\\n8n\\workspace=/workspace,/srv/ws=/workspace`` → longest prefix first."""
    pairs: list[tuple[str, str]] = []
    for item in raw.split(","):
        if "=" not in item:
            continue
        source, target = item.rsplit("=", 1)
        if source.strip() and target.strip():
            pairs.append((source.strip(), target.strip()))
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


def map_host_path(raw: str, path_map: Sequence[tuple[str, str]]) -> str:
    """Map a host path to its container path; the longest prefix wins, comparison is
    case-insensitive, and an unmapped path is returned unchanged."""
    for source, target in path_map:
        norm_raw, norm_src = raw.replace("\\", "/").lower(), source.replace("\\", "/").lower()
        if norm_raw == norm_src or norm_raw.startswith(norm_src.rstrip("/") + "/"):
            rest = raw.replace("\\", "/")[len(norm_src.rstrip("/")) :].lstrip("/")
            return str(Path(target) / rest) if rest else target
    return raw


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    claude_bin: str = "claude"
    workspace: str = "/workspace"
    allowed_roots: tuple[str, ...] = ("/workspace",)
    path_map: tuple[tuple[str, str], ...] = ()
    api_key: str = ""
    host_actions_operator_key: str = ""
    max_concurrent: int = 2
    max_run_seconds: int = 3600
    default_model: str = ""
    max_turns: int = 0
    max_budget_usd: float = 0.0
    bare: bool = False
    extra_args: tuple[str, ...] = ()
    compact_mode: str = "auto"
    compact_min_tokens: int = 120_000
    compact_instructions: str = DEFAULT_COMPACT_INSTRUCTIONS
    # Compact instructions are delivered only to the local (credential-isolating) provider,
    # so the cloud subscription's prompt stays the stock summarizer.
    local_compact_instructions: bool = True
    compact_timeout_seconds: int = 600
    think_off_after_seconds: int = 0
    think_off_after_steps: int = 0
    think_loop_repeats: int = 4
    think_loop_hold_steps: int = 12
    think_stuck_noedit_steps: int = 12
    think_stuck_errors: int = 3
    think_flag_file: str = ""
    # memory guard (mem_guard.py): kill a process that holds more than this many MB of
    # anonymous memory (0 = off) or the biggest one when the container passes this share
    # of its limit
    mem_guard_proc_mb: int = 5000
    # data-segment limit (RLIMIT_DATA) of the claude process and everything the Worker
    # starts: a script that allocates more gets MemoryError with a traceback (it can fix
    # itself) instead of an OOM kill; a memory-mapped file is not counted. 0 = off
    proc_data_limit_mb: int = 4096
    # a restore point (git_snapshot.py) of the tracked changes before every Worker run, the newest
    # git_snapshot_keep are kept; off by default (docker-compose.yml turns it on)
    worker_git_snapshot: bool = False
    git_snapshot_keep: int = 100
    mem_guard_cgroup_percent: int = 85
    # watchdog (runs.py): every interval, a run's CLI process that used more than
    # cpu_seconds of CPU since the run's last event is killed as child_hung, and one
    # with no live parent as orphan
    watchdog_interval_seconds: int = 60
    watchdog_cpu_seconds: int = 900
    # QA runs skip the CPU check (test suites burn CPU between events); each is stopped
    # as qa_timeout once it has run longer than qa_max_minutes
    qa_max_minutes: int = QA_MAX_MINUTES
    think_policy: str = ""
    think_policy_from_task: int = 1
    # Local runs priced as a cloud model by thinking mode (local_pricing.py);
    # the raw environment is kept for it
    local_pricing_env: dict[str, str] = field(default_factory=dict)
    interrupt_grace_seconds: float = 10.0
    profiles: dict[str, RoleProfile] = field(default_factory=lambda: dict(DEFAULT_PROFILES))
    tools: str = ""
    providers: dict[str, ProviderProfile] = field(default_factory=dict)
    # Activity log for the dashboard: token deltas, retention.
    live_tokens: bool = True
    event_retention_days: int = 14
    # Steer runs to ``ahawr-search`` with a PreToolUse hook (search_hook.py): exploratory greps are
    # refused until the session has used it, then once per ``search_first_every`` (0 = never again).
    search_first: bool = False
    search_first_roles: tuple[str, ...] = ("worker",)
    search_first_every: int = 6
    # Fresh-session retry: off by default (a retry compacts and resumes the old session).
    retry_fresh_session: bool = False
    retry_input_chars: int = DEFAULT_RETRY_INPUT_CHARS
    # Tool output limits (Part B): off by default so the env Claude Code gets is unchanged
    # until the operator enables them; then they are injected only on the local Worker,
    # per the values measured in docs/cold-prefill.md (Part B, section 3).
    local_tool_output_limits: bool = False
    # local runs: no billing-header hash and no auto-memory path at the start of the prompt
    local_shared_prefix: bool = True
    # Cloud (Anthropic) runs: apply the local thinking policy per attempt (cloud_think.py);
    # off by default, then the CLI's default thinking behaviour is used for the chosen model.
    cloud_think: bool = False
    # Providers in the board database (board.providers / board.provider_models): off by
    # default, the env blocks are the only source; on, the database overrides env blocks.
    providers_db: bool = False
    # Provider API keys live in this file (docs/providers.md, section 10); unset
    # means data_dir/provider-secrets.json, created lazily by the secrets store.
    secrets_file: Path | None = None
    # Writes the LiteLLM route entries from the provider database (docs/providers.md, section 13).
    litellm_sync: bool = False

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        workspace = env.get("CLAUDE_RUNNER_WORKSPACE", "/workspace").strip() or "/workspace"
        roots = _list(env.get("CLAUDE_RUNNER_ALLOWED_ROOTS")) or [workspace]
        compact_mode = env.get("CLAUDE_RUNNER_COMPACT_MODE", "auto").strip() or "auto"
        if compact_mode not in COMPACT_MODES:
            raise ValueError(f"CLAUDE_RUNNER_COMPACT_MODE must be one of {sorted(COMPACT_MODES)}")
        profiles = {}
        for role in ROLES:
            base = DEFAULT_PROFILES[role]
            prefix = f"CLAUDE_RUNNER_{role.upper()}_"
            mode = env.get(prefix + "PERMISSION_MODE", "").strip() or base.permission_mode
            if mode not in PERMISSION_MODES:
                raise ValueError(f"{prefix}PERMISSION_MODE: unknown permission mode {mode!r}")
            allowed = _list(env.get(prefix + "ALLOWED_TOOLS"))
            disallowed = _list(env.get(prefix + "DISALLOWED_TOOLS"))
            profiles[role] = replace(
                base,
                permission_mode=mode,
                allowed_tools=base.allowed_tools if allowed is None else allowed,
                disallowed_tools=base.disallowed_tools if disallowed is None else disallowed,
                append_system_prompt=env.get(prefix + "APPEND_SYSTEM_PROMPT", "").strip(),
                max_turns=_int(env, prefix + "MAX_TURNS", 0),
                tools=env.get(prefix + "TOOLS", "").strip(),
                add_dirs=_list(env.get(prefix + "ADD_DIRS")) or [],
            )
        # Empty env var = "use Claude Code's built-in summarizer" (no custom instructions);
        # unset = use the built-in default instruction text.
        if "CLAUDE_RUNNER_COMPACT_INSTRUCTIONS" in env:
            compact_instructions = env["CLAUDE_RUNNER_COMPACT_INSTRUCTIONS"].strip()
        else:
            compact_instructions = DEFAULT_COMPACT_INSTRUCTIONS
        return cls(
            data_dir=Path(env.get("CLAUDE_RUNNER_DATA_DIR", "/data")),
            claude_bin=env.get("CLAUDE_RUNNER_CLAUDE_BIN", "claude").strip() or "claude",
            workspace=workspace,
            allowed_roots=tuple(roots),
            path_map=tuple(_path_map(env.get("CLAUDE_RUNNER_PATH_MAP", ""))),
            api_key=env.get("CLAUDE_RUNNER_API_KEY", "").strip(),
            host_actions_operator_key=env.get(
                "CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY", ""
            ).strip(),
            max_concurrent=_int(env, "CLAUDE_RUNNER_MAX_CONCURRENT", 2, minimum=1),
            max_run_seconds=_int(env, "CLAUDE_RUNNER_MAX_RUN_SECONDS", 3600, minimum=1),
            default_model=env.get("CLAUDE_RUNNER_DEFAULT_MODEL", "").strip(),
            max_turns=_int(env, "CLAUDE_RUNNER_MAX_TURNS", 0),
            max_budget_usd=_float(env, "CLAUDE_RUNNER_MAX_BUDGET_USD", 0.0),
            bare=_bool(env, "CLAUDE_RUNNER_BARE", False),
            extra_args=tuple(shlex.split(env.get("CLAUDE_RUNNER_EXTRA_ARGS", ""))),
            compact_mode=compact_mode,
            compact_min_tokens=_int(env, "CLAUDE_RUNNER_COMPACT_MIN_TOKENS", 120_000),
            compact_instructions=compact_instructions,
            local_compact_instructions=_bool(env, "CLAUDE_RUNNER_LOCAL_COMPACT_INSTRUCTIONS", True),
            compact_timeout_seconds=_int(env, "CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS", 600, 1),
            think_off_after_seconds=_int(env, "CLAUDE_RUNNER_THINK_OFF_AFTER_SECONDS", 0),
            think_off_after_steps=_int(env, "CLAUDE_RUNNER_THINK_OFF_AFTER_STEPS", 0),
            think_loop_repeats=_int(env, "CLAUDE_RUNNER_THINK_LOOP_REPEATS", 4),
            think_loop_hold_steps=_int(env, "CLAUDE_RUNNER_THINK_LOOP_HOLD_STEPS", 12),
            think_flag_file=env.get("CLAUDE_RUNNER_THINK_FLAG_FILE", "").strip(),
            mem_guard_proc_mb=_int(env, "CLAUDE_RUNNER_MEM_GUARD_PROC_MB", 5000),
            think_stuck_noedit_steps=_int(env, "CLAUDE_RUNNER_THINK_STUCK_NOEDIT_STEPS", 12),
            think_stuck_errors=_int(env, "CLAUDE_RUNNER_THINK_STUCK_ERRORS", 3),
            proc_data_limit_mb=_int(env, "CLAUDE_RUNNER_PROC_DATA_LIMIT_MB", 4096),
            worker_git_snapshot=_bool(env, "CLAUDE_RUNNER_WORKER_GIT_SNAPSHOT", False),
            git_snapshot_keep=_int(env, "CLAUDE_RUNNER_GIT_SNAPSHOT_KEEP", 100, 1),
            mem_guard_cgroup_percent=_int(env, "CLAUDE_RUNNER_MEM_GUARD_CGROUP_PERCENT", 85),
            watchdog_interval_seconds=_int(env, "CLAUDE_RUNNER_WATCHDOG_INTERVAL_SECONDS", 60, 1),
            watchdog_cpu_seconds=_int(env, "CLAUDE_RUNNER_WATCHDOG_CPU_SECONDS", 900, 1),
            qa_max_minutes=_int(env, "CLAUDE_RUNNER_QA_MAX_MINUTES", QA_MAX_MINUTES, 1),
            think_policy=env.get("CLAUDE_RUNNER_THINK_POLICY", "").strip().lower(),
            think_policy_from_task=_int(env, "CLAUDE_RUNNER_THINK_POLICY_FROM_TASK", 1, 1),
            local_pricing_env={
                k: v for k, v in env.items() if k.startswith("CLAUDE_RUNNER_LOCAL_PRICE_")
            },
            profiles=profiles,
            tools=env.get("CLAUDE_RUNNER_TOOLS", "").strip(),
            providers=_providers(env),
            live_tokens=_bool(env, "CLAUDE_RUNNER_LIVE_TOKENS", True),
            event_retention_days=_int(env, "CLAUDE_RUNNER_EVENT_RETENTION_DAYS", 14),
            search_first=_bool(env, "CLAUDE_RUNNER_SEARCH_FIRST", False),
            search_first_roles=tuple(
                _list(env.get("CLAUDE_RUNNER_SEARCH_FIRST_ROLES")) or ["worker"]
            ),
            search_first_every=_int(env, "CLAUDE_RUNNER_SEARCH_FIRST_EVERY", 6),
            retry_fresh_session=_bool(env, "CLAUDE_RUNNER_RETRY_FRESH_SESSION", False),
            retry_input_chars=_int(
                env, "CLAUDE_RUNNER_RETRY_INPUT_CHARS", DEFAULT_RETRY_INPUT_CHARS, minimum=1
            ),
            local_tool_output_limits=_bool(env, "CLAUDE_RUNNER_LOCAL_TOOL_OUTPUT_LIMITS", False),
            local_shared_prefix=_bool(env, "CLAUDE_RUNNER_LOCAL_SHARED_PREFIX", True),
            cloud_think=_bool(env, "CLAUDE_RUNNER_CLOUD_THINK", False),
            providers_db=_bool(env, "CLAUDE_RUNNER_PROVIDERS_DB", False),
            secrets_file=Path(env["CLAUDE_RUNNER_SECRETS_FILE"])
            if env.get("CLAUDE_RUNNER_SECRETS_FILE", "").strip()
            else None,
            litellm_sync=_bool(env, "CLAUDE_RUNNER_LITELLM_SYNC", False),
        )

    def profile(self, role: str) -> RoleProfile:
        return self.profiles.get(role, self.profiles["generic"])

    def provider(self, name: str) -> ProviderProfile | None:
        """The configured provider for a request's ``provider`` value; None = container env."""
        return self.providers.get(provider_key(name)) if name.strip() else None

    def retry_fresh_session_for(self, provider: ProviderProfile | None) -> bool:
        """Whether a retry with a previous session starts a fresh one instead of
        compacting and resuming: enabled for the provider (else globally)."""
        return (
            provider.retry_fresh_session
            if provider is not None and provider.retry_fresh_session is not None
            else self.retry_fresh_session
        )

    def search_first_for(self, role: str, provider: ProviderProfile | None) -> bool:
        """Whether this run gets the search hook: enabled for the provider (else globally), the
        role is listed, and ``ahawr-search`` is installed (the hook's message points to it)."""
        on = (
            provider.search_first
            if provider is not None and provider.search_first is not None
            else self.search_first
        )
        return (
            bool(on)
            and role in self.search_first_roles
            and shutil.which("ahawr-search") is not None
        )

    def tools_for(self, profile: RoleProfile, provider: ProviderProfile | None) -> str:
        return profile.tools or (provider.tools if provider else "") or self.tools
