"""Per-run Codex home: the ``config.toml`` the runner writes before each Codex run.

Codex reads ``$CODEX_HOME/config.toml``. Each run gets its own directory, so no user config is
loaded, and the file names only the environment variable that holds the key (``env_key``).
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ProviderProfile
from .context_probe import autocompact_trigger

CODEX_HOME_DIR_NAME = "codex"
CONFIG_FILE_NAME = "config.toml"
PERMISSIONS_PROFILE = "ahawr"
REQUEST_MAX_RETRIES = 0
TOOL_OUTPUT_TOKEN_LIMIT = 8_000
SHELL_INCLUDE_ONLY = ("PATH", "HOME", "LANG", "TERM")
SHELL_EXCLUDE = ("*KEY*", "*TOKEN*", "*SECRET*", "*PASSWORD*")
# A glob key can only deny (Codex 0.159): .env.example stays readable by not being listed.
DENY_ENV_FILES = ("**/.env", "**/.env.local", "**/.env.*.local")
GLOB_SCAN_MAX_DEPTH = 8  # directory levels Linux expands the deny globs to (Codex warns without it)
# Tools a mission never uses; each one costs prompt tokens on every request (measured with a
# capture server: 9 tools / 17.8K chars -> 3 tools / 3.9K chars). Like Claude's --tools list.
TRIMMED_FEATURES = (
    "multi_agent",
    "goals",
    "view_image",
    "apps",
    "plugins",
    "tool_suggest",
    "skill_search",
    "image_generation",
)
COMPACT_PROMPT = (
    "Summarize the session in under 2000 tokens: the task and its acceptance criteria, the "
    "findings as file:line references, the changed files, and the checks done or not done. "
    "Do not copy tool outputs, logs, diffs or code verbatim."
)
HYPOTHESIS_LIMIT = "# HYPOTHESIS: unit and effect, codex-executor.md section 3"
HYPOTHESIS_COMPACT = "# HYPOTHESIS: exec applies it, codex-executor.md section 9"
HYPOTHESIS_SHELL = "# HYPOTHESIS: sub-keys, codex-executor.md section 6"
HYPOTHESIS_SANDBOX = "# HYPOTHESIS: roots and excludes, codex-executor.md section 4"
HYPOTHESIS_PROVIDER = "# HYPOTHESIS: effect, codex-executor.md section 3"


def _str(value: str) -> str:
    # JSON string escapes are valid TOML basic-string escapes; non-ASCII stays raw.
    return json.dumps(value, ensure_ascii=False)


def _str_list(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_str(v) for v in values) + "]"


def _top_level_lines(
    provider: ProviderProfile, role: str, model: str, window: int | None
) -> list[str]:
    sandbox = "workspace-write" if role == "worker" else "read-only"
    lines = [
        f"model = {_str(model)}",
        *([] if provider.codex_auth == "chatgpt" else [f"model_provider = {_str(provider.name)}"]),
        f"sandbox_mode = {_str(sandbox)}",
        'approval_policy = "never"',
        f"default_permissions = {_str(PERMISSIONS_PROFILE)}",
        f"compact_prompt = {_str(COMPACT_PROMPT)}",
        f"tool_output_token_limit = {TOOL_OUTPUT_TOKEN_LIMIT}  {HYPOTHESIS_LIMIT}",
    ]
    if window is not None:
        trigger = autocompact_trigger(window, provider)
        lines += [
            f"model_context_window = {window}",
            f"model_auto_compact_token_limit = {trigger}  {HYPOTHESIS_COMPACT}",
        ]
    return lines


def _table_lines(provider: ProviderProfile, workspace: str, role: str) -> list[str]:
    lines = [
        "",
        f"[permissions.{PERMISSIONS_PROFILE}.filesystem]",
        f"glob_scan_max_depth = {GLOB_SCAN_MAX_DEPTH}",
        '":root" = "read"',
        f'":project_roots" = "{"write" if role == "worker" else "read"}"',
        *(f'{_str(workspace.rstrip("/") + "/" + pattern)} = "none"' for pattern in DENY_ENV_FILES),
        "",
        f"[shell_environment_policy]  {HYPOTHESIS_SHELL}",
        'inherit = "core"',
        f"include_only = {_str_list(SHELL_INCLUDE_ONLY)}",
        f"exclude = {_str_list(SHELL_EXCLUDE)}",
    ]
    if role == "worker":
        lines += [
            "",
            f"[sandbox_workspace_write]  {HYPOTHESIS_SANDBOX}",
            f"writable_roots = {_str_list((workspace,))}",
            "exclude_slash_tmp = true",
            "exclude_tmpdir_env_var = true",
            "network_access = false",
        ]
    if provider.codex_auth == "chatgpt":  # Codex's own provider with the login, no custom table
        return lines
    lines += [
        "",
        f"[model_providers.{_str(provider.name)}]",
        f"name = {_str(provider.name)}",
        f"base_url = {_str(provider.codex_base_url)}",
        'wire_api = "responses"',
        f"request_max_retries = {REQUEST_MAX_RETRIES}  {HYPOTHESIS_PROVIDER}",
        f"stream_idle_timeout_ms = {provider.codex_stream_idle_timeout_ms}  {HYPOTHESIS_PROVIDER}",
    ]
    if provider.codex_api_key_var:
        lines.append(f"env_key = {_str(provider.codex_api_key_var)}")
    return lines


def render_config_toml(
    provider: ProviderProfile,
    role: str,
    model: str,
    workspace: str,
    window: int | None,
    trim_tools: bool = False,
) -> str:
    lines = _top_level_lines(provider, role, model, window)
    if trim_tools and provider.codex_auth != "chatgpt":
        # OpenAI's hosted search: a local engine cannot run it, ChatGPT can (it stays on there)
        lines.append(f"web_search = {_str('disabled')}")
    lines += _table_lines(provider, workspace, role)
    if trim_tools:
        lines += ["", "[features]", *(f"{name} = false" for name in TRIMMED_FEATURES)]
    return "\n".join(lines) + "\n"


def write_codex_home(run_dir: Path, config_toml: str) -> Path:
    home = run_dir / CODEX_HOME_DIR_NAME
    home.mkdir(parents=True, exist_ok=True)
    (home / CONFIG_FILE_NAME).write_text(config_toml, encoding="utf-8")
    return home
