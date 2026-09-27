"""Runner settings from ``CLAUDE_RUNNER_*`` environment variables.

Role profiles decide what Claude Code may do for each AHAWR role. The defaults keep the
Architect and the Reviewer read-only and let the Worker edit and run commands inside the
mounted workspace, while never reading ``.env`` files or committing/pushing.
"""

from __future__ import annotations

import os
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

_READ_ONLY_DENY = ["Edit", "Write", "NotebookEdit"]
_SECRET_DENY = ["Read(./.env)", "Read(./.env.*)", "Read(**/.env)", "Read(**/.env.*)"]
_GIT_DENY = ["Bash(git commit *)", "Bash(git push *)"]


@dataclass(frozen=True)
class RoleProfile:
    permission_mode: str
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    append_system_prompt: str = ""
    max_turns: int = 0
    # Built-in tool set offered to the model (`--tools`); empty = Claude Code's default set.
    tools: str = ""


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
    compact_timeout_seconds: int = 600
    interrupt_grace_seconds: float = 10.0
    profiles: dict[str, RoleProfile] = field(default_factory=lambda: dict(DEFAULT_PROFILES))

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
                tools=(env.get(prefix + "TOOLS") or env.get("CLAUDE_RUNNER_TOOLS") or "").strip(),
            )
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
            compact_timeout_seconds=_int(env, "CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS", 600, 1),
            profiles=profiles,
        )

    def profile(self, role: str) -> RoleProfile:
        return self.profiles.get(role, self.profiles["generic"])
