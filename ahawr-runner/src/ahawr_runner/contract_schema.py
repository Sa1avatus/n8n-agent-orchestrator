"""Validate provider blocks and run events against the field tables.

The tables mirror ``docs/provider-block-schema.md`` and ``docs/run-event-format.md``:
``validate_provider_block`` checks one provider block (unknown keys are allowed),
``validate_run_event`` checks one event entry (the envelope plus the fields of its kind).
Each returns a list of error strings naming the field and the problem. A missing
``schema_version`` is read as version 0 and accepted.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# A field row of a table: (field name, required, type label). A type label ending in
# "?" accepts None as the value.
FieldSpec = tuple[str, bool, str]

ENGINE_VALUES = ("claude", "codex", "hermes")

# The env keys of a provider block (docs/provider-block-schema.md): every key is
# optional, the parser converts it, unknown keys land in env and are ignored.
PROVIDER_BLOCK_FIELDS: dict[str, tuple[str, str]] = {
    "ENGINE": ("engine", "engine"),
    "TOOLS": ("tools", "str"),
    "COMPACT_MIN_TOKENS": ("compact_min_tokens", "int"),
    "COMPACT_MIN_PCT": ("compact_min_pct", "float"),
    "COMPACT_PCT": ("compact_pct", "float"),
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": ("autocompact_threshold", "float"),
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": ("autocompact_threshold", "int"),
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": ("max_output_tokens", "int"),
    "CONTEXT_PROBE_URL": ("context_probe_url", "str"),
    "CONTEXT_PROBE_KEY": ("context_probe_key", "str"),
    "HERMES_BASE_URL_VAR": ("hermes_base_url_var", "str"),
    "HERMES_API_KEY_VAR": ("hermes_api_key_var", "str"),
    "HERMES_START_TIMEOUT_MS": ("hermes_start_timeout_ms", "int"),
    "HERMES_POLL_INTERVAL_MS": ("hermes_poll_interval_ms", "int"),
    "HERMES_RUN_TIMEOUT_MS": ("hermes_run_timeout_ms", "int"),
    "HERMES_MAX_POLLS": ("hermes_max_polls", "int"),
    "CODEX_BASE_URL": ("codex_base_url", "str"),
    "CODEX_API_KEY_VAR": ("codex_api_key_var", "str"),
    "CODEX_STREAM_IDLE_TIMEOUT_MS": ("codex_stream_idle_timeout_ms", "int"),
    "PRICE_USD_PER_MILLION_TOKENS": ("price_usd_per_million_tokens", "str"),
    "ANTHROPIC_API_KEY": ("env", "str"),
    "ANTHROPIC_AUTH_TOKEN": ("env", "str"),
    "ANTHROPIC_BASE_URL": ("env", "str"),
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": ("env", "int"),
    "BASH_MAX_OUTPUT_LENGTH": ("env", "int"),
    "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS": ("env", "int"),
    "SEARCH_FIRST": ("search_first", "bool"),
    "RETRY_FRESH_SESSION": ("retry_fresh_session", "bool"),
}

# Fields every event carries (docs/run-event-format.md, "Envelope").
EVENT_ENVELOPE_FIELDS: tuple[FieldSpec, ...] = (
    ("seq", True, "int"),
    ("t", True, "float"),
    ("kind", True, "str"),
    ("agent", False, "str"),
    ("step", False, "int"),
)

# The kinds written by the Claude Code and Hermes engines and the runner bookkeeping.
EVENT_KIND_FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "prompt": (("text", True, "str"), ("resume", False, "bool")),
    "init": (
        ("model", True, "str"),
        ("cwd", True, "str"),
        ("permission_mode", True, "str"),
        ("tools", True, "list_str"),
        ("version", True, "str"),
    ),
    "user": (("text", True, "str"),),
    "thinking": (("text", True, "str"),),
    "text": (("text", True, "str"),),
    "tool_use": (
        ("id", True, "str"),
        ("name", True, "str"),
        ("input", True, "dict?"),
    ),
    "tool_result": (
        ("id", True, "str"),
        ("is_error", True, "bool"),
        ("text", True, "str"),
    ),
    "step": (
        ("step", True, "int"),
        ("model", True, "str"),
        ("started", True, "float"),
        ("input_tokens", True, "int"),
        ("cache_read_tokens", True, "int"),
        ("cache_write_tokens", True, "int"),
        ("output_tokens", True, "int"),
        ("duration_ms", False, "int"),
        ("ttft_ms", False, "int"),
        ("generation_ms", False, "int"),
        ("tokens_per_s", False, "float"),
        ("stream_buffered", False, "bool"),
    ),
    "retry": (
        ("attempt", False, "int?"),
        ("max_retries", False, "int?"),
        ("status", False, "int?"),
        ("error", False, "str?"),
        ("delay_ms", False, "int?"),
    ),
    "compact": (
        ("trigger", False, "str?"),
        ("pre_tokens", False, "int?"),
        ("post_tokens", False, "int?"),
        ("duration_ms", False, "int?"),
    ),
    "result": (
        ("subtype", True, "str?"),
        ("is_error", True, "bool"),
        ("text", True, "str"),
        ("num_turns", False, "int?"),
        ("cost_usd", False, "float?"),
        ("duration_ms", False, "int?"),
        ("duration_api_ms", False, "int?"),
        ("usage", False, "dict?"),
        ("model_usage", False, "dict?"),
    ),
    "end": (
        ("status", True, "str"),
        ("error_code", True, "str"),
        ("error", True, "str"),
        ("stderr_tail", False, "str"),
    ),
    "git_snapshot": (("ref", True, "str"),),
    "thinking_state": (
        ("thinking", True, "str"),
        ("variant", False, "str"),
        ("retry", False, "bool"),
    ),
    "thinking_switch": (
        ("thinking", True, "str"),
        ("reason", True, "str"),
        ("session_seconds", False, "float"),
        ("limit_seconds", False, "float"),
        ("steps", False, "int"),
        ("limit_steps", False, "int"),
    ),
    "context_window": (
        ("source", True, "str"),
        ("probed", False, "int?"),
        ("window", False, "int?"),
        ("compact_window", False, "int?"),
        ("compact_pct", False, "str?"),
    ),
    "qa_verdict": (
        ("role", True, "str"),
        ("status", True, "str"),
        ("summary", True, "str"),
        ("cost_usd", False, "float"),
    ),
    "qa_scenario": (
        ("scenario", False, "str"),
        ("layer", False, "str"),
        ("verdict", True, "str"),
        ("reason", False, "str"),
        ("minutes", False, "float"),
    ),
    "mem_guard": (
        ("pid", True, "int"),
        ("process", True, "str"),
        ("memory_mb", True, "int"),
        ("reason", True, "str"),
        ("command", False, "str"),
    ),
    "notice": (("text", True, "str"),),
}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_float(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _is_list_str(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_engine(value: Any) -> bool:
    return value in ENGINE_VALUES


TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "int": _is_int,
    "float": _is_float,
    "str": _is_str,
    "bool": _is_bool,
    "dict": _is_dict,
    "list_str": _is_list_str,
    "engine": _is_engine,
}


def _check_schema_version(values: dict[str, Any], label: str) -> list[str]:
    """Absent is version 0 and accepted; a present marker must be an int."""
    if "schema_version" in values and not _is_int(values["schema_version"]):
        return [
            f"field schema_version in {label}: expected int, "
            f"got {type(values['schema_version']).__name__}"
        ]
    return []


def _check_fields(
    fields: tuple[FieldSpec, ...],
    values: dict[str, Any],
    label: str,
) -> list[str]:
    """Missing required fields and wrong types; fields not in the table are ignored."""
    errors: list[str] = []
    for name, required, type_name in fields:
        if name not in values:
            if required:
                errors.append(f"missing required field {name} in {label}")
            continue
        value = values[name]
        if value is None and type_name.endswith("?"):
            continue
        if not TYPE_CHECKS[type_name.rstrip("?")](value):
            errors.append(
                f"field {name} in {label}: expected {type_name.rstrip('?')}, "
                f"got {type(value).__name__}"
            )
    return errors


def validate_provider_block(block: dict[str, Any]) -> list[str]:
    """Errors in one provider block; keys not in the table are allowed and ignored."""
    if not isinstance(block, dict):
        return [f"provider block must be a dict, got {type(block).__name__}"]
    errors = _check_schema_version(block, "provider block")
    for key, (field, type_name) in PROVIDER_BLOCK_FIELDS.items():
        if key in block and not TYPE_CHECKS[type_name](block[key]):
            errors.append(
                f"field {field} (key {key}) in provider block: expected {type_name}, "
                f"got {type(block[key]).__name__}"
            )
    return errors


def validate_run_event(event: dict[str, Any]) -> list[str]:
    """Errors in one event entry: envelope fields, then the fields of the kind."""
    if not isinstance(event, dict):
        return [f"run event must be a dict, got {type(event).__name__}"]
    errors = _check_schema_version(event, "run event")
    errors.extend(_check_fields(EVENT_ENVELOPE_FIELDS, event, "run event"))
    kind = event.get("kind")
    if not isinstance(kind, str):
        return errors
    kind_fields = EVENT_KIND_FIELDS.get(kind)
    if kind_fields is None:
        errors.append(f"unknown event kind {kind!r}")
        return errors
    errors.extend(_check_fields(kind_fields, event, f"kind {kind}"))
    return errors
