"""Codex CLI invocation (``codex exec --json``, ``codex exec resume``) and its child environment.

The flags are the ones confirmed for Codex CLI 0.159.0 (docs/codex-executor.md). The model and
the sandbox limits come from the per-run ``config.toml`` (codex_config.py), not from argv.
"""

from __future__ import annotations

import os
from pathlib import Path

from .claude_cli import UUID_RE
from .codex_config import SHELL_INCLUDE_ONLY
from .config import ProviderProfile

CODEX_HOME_VAR = "CODEX_HOME"


def build_command(
    codex_bin: str,
    *,
    workspace: str,
    role: str,
    last_message_path: str,
    prompt: str,
) -> list[str]:
    return [
        codex_bin,
        "exec",
        "--json",
        "-C",
        workspace,
        "-o",
        last_message_path,
        "--skip-git-repo-check",
        prompt,
    ]


def build_app_server_command(codex_bin: str) -> list[str]:
    """The JSON-RPC server over stdio; the session, prompt and workspace go in as requests."""
    return [codex_bin, "app-server", "--stdio"]


def build_resume_command(
    codex_bin: str,
    *,
    session_id: str,
    role: str,
    last_message_path: str,
    prompt: str,
) -> list[str]:
    if not UUID_RE.match(session_id):
        raise ValueError(f"not a Codex session id: {session_id!r}")
    # exec resume takes no -C. No -s on either command: an explicit sandbox makes Codex ignore the
    # permissions profile (the .env denies) of the run's config.toml, which also sets sandbox_mode.
    return [
        codex_bin,
        "exec",
        "resume",
        "--json",
        "-o",
        last_message_path,
        "--skip-git-repo-check",
        session_id,
        prompt,
    ]


def child_env(provider: ProviderProfile, codex_home: Path) -> dict[str, str]:
    """Only the shell basics, the run's Codex home and the provider's one API key variable."""
    env = {key: os.environ[key] for key in SHELL_INCLUDE_ONLY if key in os.environ}
    key_var = provider.codex_api_key_var
    if key_var and key_var in provider.env:
        env[key_var] = provider.env[key_var]
    env[CODEX_HOME_VAR] = str(codex_home)
    return env
