"""Runner settings from ``CLAUDE_RUNNER_*`` environment variables.

Role profiles decide what Claude Code may do for each AHAWR role. The defaults keep the
Architect and the Reviewer read-only and let the Worker edit and run commands inside the
mounted workspace, while never reading ``.env`` files or committing/pushing.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from ahawr_runner.local_pricing import parse_rates

# The fallback profile role used when a requested role has no profile (Settings.profile).
GENERIC_PROFILE_NAME = "generic"
ROLES = ("architect", "worker", "reviewer", "qa", "qa_agent", GENERIC_PROFILE_NAME)
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
    "sed -n",  # prints a line range; sed -i and redirections stay denied
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
# ENGINE selects the agent CLI for the provider: claude (Claude Code) or codex (Codex CLI);
# hermes is accepted only when the T004A flag (hermes_engine_enabled) is ON. The CODEX_* and
# HERMES_* keys and PRICE_USD_PER_MILLION_TOKENS configure the engine blocks only; none of them
# reach the Claude Code process. The HERMES_* keys name the environment variables the engine
# reads (names, never values), and its timeouts in milliseconds.
PROVIDER_RUNNER_KEYS = {
    "TOOLS",
    "COMPACT_MIN_TOKENS",
    "COMPACT_MIN_PCT",
    "CONTEXT_PROBE_URL",
    "CONTEXT_PROBE_KEY",
    "COMPACT_PCT",
    "SEARCH_FIRST",
    "RETRY_FRESH_SESSION",
    "ENGINE",
    "CODEX_BASE_URL",
    "CODEX_API_KEY_VAR",
    "CODEX_STREAM_IDLE_TIMEOUT_MS",
    "HERMES_BASE_URL_VAR",
    "HERMES_API_KEY_VAR",
    "HERMES_START_TIMEOUT_MS",
    "HERMES_POLL_INTERVAL_MS",
    "HERMES_RUN_TIMEOUT_MS",
    "HERMES_MAX_POLLS",
    "PRICE_USD_PER_MILLION_TOKENS",
}
ENGINES = ("claude", "codex")
DEFAULT_ENGINE = "claude"
DEFAULT_CODEX_BASE_URL = "https://api.openai.com/v1"
DEFAULT_CODEX_API_KEY_VAR = "OPENAI_API_KEY"
CODEX_AUTH_MODES = ("api_key", "chatgpt")
DEFAULT_CODEX_AUTH = "api_key"
# where `codex login` (run with this CODEX_HOME) keeps the ChatGPT login (in the home volume)
DEFAULT_CODEX_LOGIN_HOME = "/home/node/.claude/codex-login"
DEFAULT_CODEX_STREAM_IDLE_TIMEOUT_MS = 300_000
# Hermes defaults. Names come from .env.example (HERMES_API_URL, API_SERVER_KEY); the poll
# interval is the docs/hermes-engine.md default (poll 3 s). Start and run timeouts are chosen
# defaults, not values recorded in the docs.
DEFAULT_HERMES_BASE_URL_VAR = "HERMES_API_URL"
DEFAULT_HERMES_API_KEY_VAR = "API_SERVER_KEY"
DEFAULT_HERMES_START_TIMEOUT_MS = 10_000
DEFAULT_HERMES_POLL_INTERVAL_MS = 3_000
DEFAULT_HERMES_RUN_TIMEOUT_MS = 3_600_000

# Character budget for the fresh-session retry input (task, review findings, previous report,
# previous session's digest, newest last). The digest fills most of it; it is built at roughly
# budget - 4000, leaving room for the three other parts.
DEFAULT_RETRY_INPUT_CHARS = 32_000

# Defaults of env-backed Settings fields, named once so the dataclass default and the from_env
# fallback share the same value: the /compact time limit in seconds, the watchdog scan interval
# in seconds, and the loopback URL of the think proxy inside the runner container.
DEFAULT_COMPACT_TIMEOUT_SECONDS = 600
DEFAULT_WATCHDOG_INTERVAL_SECONDS = 60
DEFAULT_THINK_PROXY_URL = "http://127.0.0.1:8700"

# Context-window limits the local provider's compaction settings must stay inside. The local
# model's window is 65536 tokens; the autocompact threshold must leave room for one large
# tool output (~20K tokens, saved to a file by BASH_MAX_OUTPUT_LENGTH and similar limits)
# plus the generation limit, or a run could exceed the window before the next compaction.
AUTO_COMPACT_WINDOW = 65_536
AUTO_COMPACT_TOOL_RESERVE = 20_000
DEFAULT_MAX_OUTPUT_TOKENS = 8_192
QA_MAX_MINUTES = 20
PROVIDERS_RELOAD_POLL_SECONDS = 30
# QA complexity gate (qa_complexity.py): thresholds a function not named in the
# baseline is checked against; C901 complexity is a mccabe score (branches and
# conditions), length in lines, nesting in block levels.
DEFAULT_QA_COMPLEXITY_MAX_COMPLEXITY = 10
DEFAULT_QA_COMPLEXITY_MAX_LENGTH_LINES = 40
DEFAULT_QA_COMPLEXITY_MAX_NESTING_DEPTH = 3
# Risk tiers (risk_tiers.tier_of): ordered fnmatch patterns over repo-root-relative
# paths, first match wins. Core = a wrong change breaks mission execution (ops
# patches, dashboard auth, roles, engine error rules, run failure handling,
# Live State readers); supporting = docs, UI texts, tests.
DEFAULT_RISK_TIER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("data-tables/ops/*_patch.py", "core"),
    ("*/dashboard_auth*", "core"),
    ("*/roles_config.py", "core"),
    ("*/board_engine*", "core"),
    ("*/runs.py", "core"),
    ("*/board_ops_livestate.py", "core"),
    ("*docs/*", "supporting"),
    ("*/board_i18n_*.json", "supporting"),
    ("*/tests/*", "supporting"),
)


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
    # Agent CLI that runs this provider's runs: "claude", "codex", or "hermes" (with the
    # hermes_engine_enabled flag ON) — the engine block fields name the env variables and
    # timeouts the engine reads (engine modules land in later tasks).
    engine: str = DEFAULT_ENGINE
    codex_base_url: str = DEFAULT_CODEX_BASE_URL
    # Name of the environment variable that holds the key; "" omits env_key in the Codex config.
    codex_api_key_var: str = DEFAULT_CODEX_API_KEY_VAR
    codex_stream_idle_timeout_ms: int = DEFAULT_CODEX_STREAM_IDLE_TIMEOUT_MS
    # "chatgpt": Codex signs in with the ChatGPT subscription (auth.json of codex_login_home is
    # copied into each run's Codex home) and uses its own provider; "api_key": the key variable.
    codex_auth: str = DEFAULT_CODEX_AUTH
    codex_login_home: str = DEFAULT_CODEX_LOGIN_HOME
    # Hermes engine: env variable NAMES (never values), and timeouts in milliseconds.
    hermes_base_url_var: str = DEFAULT_HERMES_BASE_URL_VAR
    hermes_api_key_var: str = DEFAULT_HERMES_API_KEY_VAR
    hermes_start_timeout_ms: int = DEFAULT_HERMES_START_TIMEOUT_MS
    hermes_poll_interval_ms: int = DEFAULT_HERMES_POLL_INTERVAL_MS
    hermes_run_timeout_ms: int = DEFAULT_HERMES_RUN_TIMEOUT_MS
    # Polls allowed before the run times out; derived from the run timeout and the poll
    # interval when the block does not name HERMES_MAX_POLLS.
    hermes_max_polls: int = DEFAULT_HERMES_RUN_TIMEOUT_MS // DEFAULT_HERMES_POLL_INTERVAL_MS
    # USD per 1,000,000 tokens: input, output, cache read, cache write; None = no price.
    price_usd_per_million_tokens: tuple[float, float, float, float] | None = None

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


def _engine(name: str, raw: str, hermes_engine_enabled: bool) -> str:
    engine = raw.strip() or DEFAULT_ENGINE
    accepted = (*ENGINES, "hermes") if hermes_engine_enabled else ENGINES
    if engine not in accepted:
        raise ValueError(
            f"provider {name}: ENGINE must be one of {', '.join(accepted)}, got {raw!r}"
        )
    return engine


def _codex_auth(name: str, raw: str) -> str:
    mode = raw.strip() or DEFAULT_CODEX_AUTH
    if mode not in CODEX_AUTH_MODES:
        raise ValueError(
            f"provider {name}: CODEX_AUTH must be one of {', '.join(CODEX_AUTH_MODES)}, got {raw!r}"
        )
    return mode


def _codex_base_url(name: str, raw: str) -> str:
    url = raw.strip() or DEFAULT_CODEX_BASE_URL
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"provider {name}: CODEX_BASE_URL must be an http(s) URL, got {raw!r}")
    return url


def _env_var_name(name: str, key: str, raw: str) -> str:
    """A block key whose value is the NAME of an environment variable, never its value."""
    var = raw.strip()
    if var and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", var):
        raise ValueError(
            f"provider {name}: {key} must be an environment variable name, got {raw!r}"
        )
    return var


def _positive_ms(name: str, key: str, raw: str, default: int) -> int:
    text = raw.strip()
    if not text:
        return default
    if not re.fullmatch(r"[0-9]+", text) or int(text) == 0:
        raise ValueError(
            f"provider {name}: {key} must be a positive integer of milliseconds, got {raw!r}"
        )
    return int(text)


def _hermes_max_polls(name: str, raw: str, run_timeout_ms: int, poll_interval_ms: int) -> int:
    text = raw.strip()
    if text:
        if not re.fullmatch(r"[0-9]+", text) or int(text) == 0:
            raise ValueError(
                f"provider {name}: HERMES_MAX_POLLS must be a positive integer, got {raw!r}"
            )
        return int(text)
    # Derived when the block does not name it: the polls that fit in the run timeout.
    return max(1, run_timeout_ms // poll_interval_ms)


def _price_usd_per_million_tokens(name: str, raw: str) -> tuple[float, float, float, float] | None:
    text = raw.strip()
    if not text:
        return None
    rates = parse_rates(text)
    if rates is None:
        raise ValueError(
            f"provider {name}: PRICE_USD_PER_MILLION_TOKENS must be four non-negative numbers "
            f"(input,output,cache_read,cache_write), got {raw!r}"
        )
    return rates


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


def _providers(
    env: dict[str, str], hermes_engine_enabled: bool = False
) -> dict[str, ProviderProfile]:
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
        start_timeout_ms = _positive_ms(
            name, "HERMES_START_TIMEOUT_MS", block.get("HERMES_START_TIMEOUT_MS", ""),
            DEFAULT_HERMES_START_TIMEOUT_MS,
        )
        poll_interval_ms = _positive_ms(
            name, "HERMES_POLL_INTERVAL_MS", block.get("HERMES_POLL_INTERVAL_MS", ""),
            DEFAULT_HERMES_POLL_INTERVAL_MS,
        )
        run_timeout_ms = _positive_ms(
            name, "HERMES_RUN_TIMEOUT_MS", block.get("HERMES_RUN_TIMEOUT_MS", ""),
            DEFAULT_HERMES_RUN_TIMEOUT_MS,
        )
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
            engine=_engine(name, block.get("ENGINE", ""), hermes_engine_enabled),
            codex_base_url=_codex_base_url(name, block.get("CODEX_BASE_URL", "")),
            codex_api_key_var=_env_var_name(
                name, "CODEX_API_KEY_VAR", block.get("CODEX_API_KEY_VAR", DEFAULT_CODEX_API_KEY_VAR)
            ),
            codex_auth=_codex_auth(name, block.get("CODEX_AUTH", "")),
            codex_login_home=block.get("CODEX_LOGIN_HOME", "").strip() or DEFAULT_CODEX_LOGIN_HOME,
            codex_stream_idle_timeout_ms=_positive_ms(
                name,
                "CODEX_STREAM_IDLE_TIMEOUT_MS",
                block.get("CODEX_STREAM_IDLE_TIMEOUT_MS", ""),
                DEFAULT_CODEX_STREAM_IDLE_TIMEOUT_MS,
            ),
            hermes_base_url_var=_env_var_name(
                name,
                "HERMES_BASE_URL_VAR",
                block.get("HERMES_BASE_URL_VAR", DEFAULT_HERMES_BASE_URL_VAR),
            ),
            hermes_api_key_var=_env_var_name(
                name,
                "HERMES_API_KEY_VAR",
                block.get("HERMES_API_KEY_VAR", DEFAULT_HERMES_API_KEY_VAR),
            ),
            hermes_start_timeout_ms=start_timeout_ms,
            hermes_poll_interval_ms=poll_interval_ms,
            hermes_run_timeout_ms=run_timeout_ms,
            hermes_max_polls=_hermes_max_polls(
                name, block.get("HERMES_MAX_POLLS", ""), run_timeout_ms, poll_interval_ms
            ),
            price_usd_per_million_tokens=_price_usd_per_million_tokens(
                name, block.get("PRICE_USD_PER_MILLION_TOKENS", "")
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
    # qa_agent (docs/qa-agent.md): read-only, reads PNG files with Read; no writes, no
    # shell commands. The tools set is restricted to Read, Glob and Grep.
    "qa_agent": RoleProfile(
        "dontAsk",
        disallowed_tools=_READ_ONLY_DENY + _SECRET_DENY,
        tools="Read,Glob,Grep",
        add_dirs=["/qa-runs"],  # the captures of the screens live outside the working directory
    ),
    GENERIC_PROFILE_NAME: RoleProfile("dontAsk", disallowed_tools=_READ_ONLY_DENY + _SECRET_DENY),
}


def _list(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def _name_pairs(raw: str) -> dict[str, str]:
    """'strata=sonnet, other=haiku' -> {'strata': 'sonnet', 'other': 'haiku'} (lower case)."""
    pairs = (item.split("=", 1) for item in re.split(r"[,;\s]+", raw) if "=" in item)
    return {k.strip().lower(): v.strip().lower() for k, v in pairs if k.strip() and v.strip()}


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


def _sampling_by_provider(raw: str) -> dict[str, dict[str, float]]:
    """``{"strata": {"temperature": 0.6}}`` -> the same without values that are no numbers; an
    empty text is no setting, a text that is no JSON object is an error."""
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("CLAUDE_RUNNER_PROXY_SAMPLING_JSON must be a JSON object")
    return {
        str(name).lower(): {
            k: float(v)
            for k, v in keys.items()
            if isinstance(v, int | float) and not isinstance(v, bool)
        }
        for name, keys in data.items()
        if isinstance(keys, dict)
    }


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


# Row caps of the board-ops store reads (board_ops_store.py, board_ops_store_pg.py): how many rows
# a command list, changelog, queue-log tail or host-snapshot tail returns when the caller passes no
# limit.  Env-backed so an operator can widen a tail without touching code; the defaults are exactly
# the literals the stores used before this change.
OPS_COMMANDS_DEFAULT_ROWS = _int(
    dict(os.environ), "CLAUDE_RUNNER_OPS_COMMANDS_DEFAULT_ROWS", 200, 1
)
OPS_CHANGELOG_DEFAULT_ROWS = _int(
    dict(os.environ), "CLAUDE_RUNNER_CHANGELOG_DEFAULT_ROWS", 200, 1
)
OPS_QUEUE_LOG_MAX_ROWS = _int(
    dict(os.environ), "CLAUDE_RUNNER_OPS_QUEUE_LOG_MAX_ROWS", 1000, 1
)
OPS_SNAPSHOT_MAX_ROWS = _int(
    dict(os.environ), "CLAUDE_RUNNER_OPS_SNAPSHOT_MAX_ROWS", 100, 1
)

# Dashboard defaults (dashboard.py): the CLI --port of the dashboard container and the row caps of
# the runs-list and search routes.  Env-backed like the ops row caps; the defaults are exactly the
# literals those routes used before this change.  AHAWR_DASHBOARD_BIND names the published address
# (dashboard_auth_config.py), not the host the container listens on inside, so DASHBOARD_BIND_HOST
# has no env variable of its own.
DASHBOARD_PORT = _int(
    dict(os.environ), "CLAUDE_RUNNER_DASHBOARD_PORT", 8701, 1
)
DASHBOARD_BIND_HOST = "0.0.0.0"
RUNS_LIST_DEFAULT_ROWS = _int(
    dict(os.environ), "CLAUDE_RUNNER_RUNS_LIST_DEFAULT_ROWS", 100, 1
)
RUNS_LIST_MAX_ROWS = _int(
    dict(os.environ), "CLAUDE_RUNNER_RUNS_LIST_MAX_ROWS", 500, 1
)
SEARCH_DEFAULT_ROWS = _int(
    dict(os.environ), "CLAUDE_RUNNER_SEARCH_DEFAULT_ROWS", 50, 1
)
SEARCH_MAX_ROWS = _int(
    dict(os.environ), "CLAUDE_RUNNER_SEARCH_MAX_ROWS", 200, 1
)


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
    compact_timeout_seconds: int = DEFAULT_COMPACT_TIMEOUT_SECONDS
    think_off_after_seconds: int = 0
    think_off_after_steps: int = 0
    think_loop_repeats: int = 4
    think_loop_hold_steps: int = 12
    think_stuck_noedit_steps: int = 12
    think_stuck_errors: int = 3
    think_flag_file: str = ""
    # a run whose provider endpoint reports an engine that ignores the client's thinking settings
    # (think_proxy.ENGINE_SERVICES) goes through the runner's think proxy; off by default
    think_proxy: bool = False
    think_proxy_url: str = DEFAULT_THINK_PROXY_URL
    # the think proxy also reads the streamed reply (call_net.py): a tool call the engine returned
    # as text becomes a tool_use block; with feedback an unreadable one becomes a request to send it
    # again; both off by default.  sampling: provider name -> temperature/top_p/top_k/min_p to set
    think_proxy_repair: bool = False
    think_proxy_feedback: bool = False
    think_proxy_sampling: dict[str, dict[str, float]] = field(default_factory=dict)
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
    watchdog_interval_seconds: int = DEFAULT_WATCHDOG_INTERVAL_SECONDS
    watchdog_cpu_seconds: int = 900
    # QA runs skip the CPU check (test suites burn CPU between events); each is stopped
    # as qa_timeout once it has run longer than qa_max_minutes
    qa_max_minutes: int = QA_MAX_MINUTES
    think_policy: str = ""
    think_policy_from_task: int = 1
    # Local runs priced as a cloud model by thinking mode (local_pricing.py);
    # the raw environment is kept for it
    local_pricing_env: dict[str, str] = field(default_factory=dict)
    # provider name -> price profile of local_pricing.py: the runs of an engine that costs
    # nothing per token are priced as that cloud model whatever the thinking mode
    flat_price_profiles: dict[str, str] = field(default_factory=dict)
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
    # Give the local Worker the ahawr_search MCP tool (claude_cli.mcp_search_args); off by default.
    mcp_search: bool = False
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
    # Hermes engine (docs/hermes-engine.md): off by default; with the flag off the accepted
    # ENGINE values and their error messages stay as today until the engine module lands.
    hermes_engine_enabled: bool = False
    # Codex runs through `codex app-server` (codex_appserver.py): reasoning, per-request tokens and
    # cache straight from the client. Off by default: the runs use `codex exec --json` as before.
    codex_app_server: bool = False
    # Hermes runs read reasoning, tool calls and per-request tokens from the gateway's session API
    # (hermes_session.py) instead of the run events plus Strata telemetry. Off by default.
    hermes_session_stats: bool = False
    # Codex without the tools a mission never uses (codex_config.TRIMMED_FEATURES), as Claude gets
    # --tools. Off by default: the config.toml is unchanged.
    codex_trim_tools: bool = False
    # Hermes toolsets of the api_server platform, comma separated (e.g. terminal,file,web).
    # Empty = Hermes' own default (about 38 tools).
    hermes_toolsets: str = ""
    # The Hermes gateway as a child process of the runner (hermes_gateway.py): off by default;
    # needs the image built with INSTALL_HERMES=true and CLAUDE_RUNNER_HERMES_GATEWAY_KEY set.
    hermes_gateway_enabled: bool = False
    hermes_gateway_bin: str = "/opt/hermes/venv/bin/hermes"
    # Providers in the board database (board.providers / board.provider_models): off by
    # default, the env blocks are the only source; on, the database overrides env blocks.
    providers_db: bool = False
    # Provider API keys live in this file (docs/providers.md, section 10); unset
    # means data_dir/provider-secrets.json, created lazily by the secrets store.
    secrets_file: Path | None = None
    # Writes the LiteLLM route entries from the provider database (docs/providers.md, section 13).
    litellm_sync: bool = False
    # QA complexity gate (qa_complexity.py): off by default; on, the gate compares function
    # length, nesting and ruff's C901 complexity with the saved baseline (equal is not worse).
    qa_complexity_enabled: bool = False
    # Thresholds for a function the baseline does not name; units are in the names.
    qa_complexity_max_complexity: int = DEFAULT_QA_COMPLEXITY_MAX_COMPLEXITY
    qa_complexity_max_length_lines: int = DEFAULT_QA_COMPLEXITY_MAX_LENGTH_LINES
    qa_complexity_max_nesting_depth: int = DEFAULT_QA_COMPLEXITY_MAX_NESTING_DEPTH
    # The saved baseline JSON (file -> function -> metrics); unset = no baseline file.
    qa_complexity_baseline_path: Path | None = None
    # Risk tiers (risk_tiers.tier_of): off by default; on, tier_of classifies a
    # changed path with risk_tier_patterns (first match wins, unmatched = "core").
    risk_tiers_enabled: bool = False
    risk_tier_patterns: tuple[tuple[str, str], ...] = DEFAULT_RISK_TIER_PATTERNS

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
            tools_env = env.get(prefix + "TOOLS", "").strip()
            tools = base.tools if tools_env == "" else tools_env
            profiles[role] = replace(
                base,
                permission_mode=mode,
                allowed_tools=base.allowed_tools if allowed is None else allowed,
                disallowed_tools=base.disallowed_tools if disallowed is None else disallowed,
                append_system_prompt=env.get(prefix + "APPEND_SYSTEM_PROMPT", "").strip(),
                max_turns=_int(env, prefix + "MAX_TURNS", 0),
                tools=tools,
                add_dirs=_list(env.get(prefix + "ADD_DIRS")) or base.add_dirs,
            )
        # Empty env var = "use Claude Code's built-in summarizer" (no custom instructions);
        # unset = use the built-in default instruction text.
        if "CLAUDE_RUNNER_COMPACT_INSTRUCTIONS" in env:
            compact_instructions = env["CLAUDE_RUNNER_COMPACT_INSTRUCTIONS"].strip()
        else:
            compact_instructions = DEFAULT_COMPACT_INSTRUCTIONS
        hermes_engine_enabled = _bool(env, "CLAUDE_RUNNER_HERMES_ENGINE_ENABLED", False)
        hermes_gateway_enabled = _bool(env, "CLAUDE_RUNNER_HERMES_GATEWAY", False)
        return cls(
            codex_app_server=_bool(env, "CLAUDE_RUNNER_CODEX_APP_SERVER", False),
            hermes_session_stats=_bool(env, "CLAUDE_RUNNER_HERMES_SESSION_STATS", False),
            codex_trim_tools=_bool(env, "CLAUDE_RUNNER_CODEX_TRIM_TOOLS", False),
            hermes_toolsets=env.get("CLAUDE_RUNNER_HERMES_TOOLSETS", "").strip(),
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
            compact_timeout_seconds=_int(
                env, "CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS", DEFAULT_COMPACT_TIMEOUT_SECONDS, 1
            ),
            think_off_after_seconds=_int(env, "CLAUDE_RUNNER_THINK_OFF_AFTER_SECONDS", 0),
            think_off_after_steps=_int(env, "CLAUDE_RUNNER_THINK_OFF_AFTER_STEPS", 0),
            think_loop_repeats=_int(env, "CLAUDE_RUNNER_THINK_LOOP_REPEATS", 4),
            think_loop_hold_steps=_int(env, "CLAUDE_RUNNER_THINK_LOOP_HOLD_STEPS", 12),
            think_stuck_noedit_steps=_int(env, "CLAUDE_RUNNER_THINK_STUCK_NOEDIT_STEPS", 12),
            think_stuck_errors=_int(env, "CLAUDE_RUNNER_THINK_STUCK_ERRORS", 3),
            think_flag_file=env.get("CLAUDE_RUNNER_THINK_FLAG_FILE", "").strip(),
            think_proxy=_bool(env, "CLAUDE_RUNNER_THINK_PROXY", False),
            think_proxy_repair=_bool(env, "CLAUDE_RUNNER_PROXY_CALL_REPAIR", False),
            think_proxy_feedback=_bool(env, "CLAUDE_RUNNER_PROXY_CALL_FEEDBACK", False),
            think_proxy_sampling=_sampling_by_provider(
                env.get("CLAUDE_RUNNER_PROXY_SAMPLING_JSON", "")
            ),
            think_proxy_url=(
                env.get("CLAUDE_RUNNER_THINK_PROXY_URL", DEFAULT_THINK_PROXY_URL).strip()
            ),
            mem_guard_proc_mb=_int(env, "CLAUDE_RUNNER_MEM_GUARD_PROC_MB", 5000),
            proc_data_limit_mb=_int(env, "CLAUDE_RUNNER_PROC_DATA_LIMIT_MB", 4096),
            worker_git_snapshot=_bool(env, "CLAUDE_RUNNER_WORKER_GIT_SNAPSHOT", False),
            git_snapshot_keep=_int(env, "CLAUDE_RUNNER_GIT_SNAPSHOT_KEEP", 100, 1),
            mem_guard_cgroup_percent=_int(env, "CLAUDE_RUNNER_MEM_GUARD_CGROUP_PERCENT", 85),
            watchdog_interval_seconds=_int(
                env, "CLAUDE_RUNNER_WATCHDOG_INTERVAL_SECONDS", DEFAULT_WATCHDOG_INTERVAL_SECONDS, 1
            ),
            watchdog_cpu_seconds=_int(env, "CLAUDE_RUNNER_WATCHDOG_CPU_SECONDS", 900, 1),
            qa_max_minutes=_int(env, "CLAUDE_RUNNER_QA_MAX_MINUTES", QA_MAX_MINUTES, 1),
            think_policy=env.get("CLAUDE_RUNNER_THINK_POLICY", "").strip().lower(),
            think_policy_from_task=_int(env, "CLAUDE_RUNNER_THINK_POLICY_FROM_TASK", 1, 1),
            local_pricing_env={
                k: v for k, v in env.items() if k.startswith("CLAUDE_RUNNER_LOCAL_PRICE_")
            },
            flat_price_profiles=_name_pairs(env.get("CLAUDE_RUNNER_FLAT_PRICE_PROFILES", "")),
            profiles=profiles,
            tools=env.get("CLAUDE_RUNNER_TOOLS", "").strip(),
            providers=_providers(env, hermes_engine_enabled),
            live_tokens=_bool(env, "CLAUDE_RUNNER_LIVE_TOKENS", True),
            event_retention_days=_int(env, "CLAUDE_RUNNER_EVENT_RETENTION_DAYS", 14),
            search_first=_bool(env, "CLAUDE_RUNNER_SEARCH_FIRST", False),
            search_first_roles=tuple(
                _list(env.get("CLAUDE_RUNNER_SEARCH_FIRST_ROLES")) or ["worker"]
            ),
            search_first_every=_int(env, "CLAUDE_RUNNER_SEARCH_FIRST_EVERY", 6),
            mcp_search=_bool(env, "CLAUDE_RUNNER_MCP_SEARCH", False),
            retry_fresh_session=_bool(env, "CLAUDE_RUNNER_RETRY_FRESH_SESSION", False),
            retry_input_chars=_int(
                env, "CLAUDE_RUNNER_RETRY_INPUT_CHARS", DEFAULT_RETRY_INPUT_CHARS, minimum=1
            ),
            local_tool_output_limits=_bool(env, "CLAUDE_RUNNER_LOCAL_TOOL_OUTPUT_LIMITS", False),
            local_shared_prefix=_bool(env, "CLAUDE_RUNNER_LOCAL_SHARED_PREFIX", True),
            cloud_think=_bool(env, "CLAUDE_RUNNER_CLOUD_THINK", False),
            hermes_engine_enabled=hermes_engine_enabled,
            hermes_gateway_enabled=hermes_gateway_enabled,
            hermes_gateway_bin=env.get("CLAUDE_RUNNER_HERMES_GATEWAY_BIN", "").strip()
            or "/opt/hermes/venv/bin/hermes",
            providers_db=_bool(env, "CLAUDE_RUNNER_PROVIDERS_DB", False),
            secrets_file=Path(env["CLAUDE_RUNNER_SECRETS_FILE"])
            if env.get("CLAUDE_RUNNER_SECRETS_FILE", "").strip()
            else None,
            litellm_sync=_bool(env, "CLAUDE_RUNNER_LITELLM_SYNC", False),
            qa_complexity_enabled=_bool(env, "CLAUDE_RUNNER_QA_COMPLEXITY_ENABLED", False),
            qa_complexity_max_complexity=_int(
                env,
                "CLAUDE_RUNNER_QA_COMPLEXITY_MAX_COMPLEXITY",
                DEFAULT_QA_COMPLEXITY_MAX_COMPLEXITY,
                minimum=1,
            ),
            qa_complexity_max_length_lines=_int(
                env,
                "CLAUDE_RUNNER_QA_COMPLEXITY_MAX_LENGTH_LINES",
                DEFAULT_QA_COMPLEXITY_MAX_LENGTH_LINES,
                minimum=1,
            ),
            qa_complexity_max_nesting_depth=_int(
                env,
                "CLAUDE_RUNNER_QA_COMPLEXITY_MAX_NESTING_DEPTH",
                DEFAULT_QA_COMPLEXITY_MAX_NESTING_DEPTH,
            ),
            qa_complexity_baseline_path=(
                Path(env["CLAUDE_RUNNER_QA_COMPLEXITY_BASELINE_PATH"])
                if env.get("CLAUDE_RUNNER_QA_COMPLEXITY_BASELINE_PATH", "").strip()
                else None
            ),
            risk_tiers_enabled=_bool(env, "CLAUDE_RUNNER_RISK_TIERS_ENABLED", False),
        )

    def profile(self, role: str) -> RoleProfile:
        return self.profiles.get(role, self.profiles[GENERIC_PROFILE_NAME])

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
