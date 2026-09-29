"""Runner settings from ``CLAUDE_RUNNER_*`` environment variables.

Role profiles decide what Claude Code may do for each AHAWR role. The defaults keep the
Architect and the Reviewer read-only and let the Worker edit and run commands inside the
mounted workspace, while never reading ``.env`` files or committing/pushing.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field, replace
from pathlib import Path

ROLES = ("architect", "worker", "reviewer", "generic")
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
_GIT_DENY = ["Bash(git commit *)", "Bash(git push *)"]


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
PROVIDER_RUNNER_KEYS = {"TOOLS", "COMPACT_MIN_TOKENS"}

# Context-window limits the local provider's compaction settings must stay inside. The local
# model's window is 65536 tokens; the autocompact threshold must leave room for one large
# tool output (~20K tokens, saved to a file by BASH_MAX_OUTPUT_LENGTH and similar limits)
# plus the generation limit, or a run could exceed the window before the next compaction.
AUTO_COMPACT_WINDOW = 65_536
AUTO_COMPACT_TOOL_RESERVE = 20_000
DEFAULT_MAX_OUTPUT_TOKENS = 8_192


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

    @property
    def isolates_credentials(self) -> bool:
        return any(key in self.env for key in CREDENTIAL_VARS)


def provider_key(name: str) -> str:
    """``custom:llama-local`` / ``llama-local`` → ``CUSTOM_LLAMA_LOCAL`` / ``LLAMA_LOCAL``."""
    return re.sub(r"[^A-Z0-9]+", "_", name.strip().upper()).strip("_")


# The precompute buffer the binary subtracts from the effective window before
# applying the threshold (hardcoded in the Claude Code bundle).
_AUTO_COMPACT_PRECOMPUTE_BUFFER = 13_000


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
        return w - min(max_output, AUTO_COMPACT_TOOL_RESERVE) - _AUTO_COMPACT_PRECOMPUTE_BUFFER
    return None


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
        )
    return providers


DEFAULT_PROFILES: dict[str, RoleProfile] = {
    "architect": RoleProfile("dontAsk", disallowed_tools=_READ_ONLY_DENY + _SECRET_DENY),
    "worker": RoleProfile("bypassPermissions", disallowed_tools=_SECRET_DENY + _GIT_DENY),
    "reviewer": RoleProfile("dontAsk", disallowed_tools=_READ_ONLY_DENY + _SECRET_DENY),
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


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    claude_bin: str = "claude"
    workspace: str = "/workspace"
    allowed_roots: tuple[str, ...] = ("/workspace",)
    path_map: tuple[tuple[str, str], ...] = ()
    api_key: str = ""
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
    interrupt_grace_seconds: float = 10.0
    profiles: dict[str, RoleProfile] = field(default_factory=lambda: dict(DEFAULT_PROFILES))
    tools: str = ""
    providers: dict[str, ProviderProfile] = field(default_factory=dict)
    # Activity log for the dashboard: token deltas, retention.
    live_tokens: bool = True
    event_retention_days: int = 14

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
            profiles=profiles,
            tools=env.get("CLAUDE_RUNNER_TOOLS", "").strip(),
            providers=_providers(env),
            live_tokens=_bool(env, "CLAUDE_RUNNER_LIVE_TOKENS", True),
            event_retention_days=_int(env, "CLAUDE_RUNNER_EVENT_RETENTION_DAYS", 14),
        )

    def profile(self, role: str) -> RoleProfile:
        return self.profiles.get(role, self.profiles["generic"])

    def provider(self, name: str) -> ProviderProfile | None:
        """The configured provider for a request's ``provider`` value; None = container env."""
        return self.providers.get(provider_key(name)) if name.strip() else None

    def tools_for(self, profile: RoleProfile, provider: ProviderProfile | None) -> str:
        return profile.tools or (provider.tools if provider else "") or self.tools
