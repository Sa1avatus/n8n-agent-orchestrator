from __future__ import annotations

import json
import math
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

MODEL_SEPARATOR = "/"
OPTIONS_KEY = "options"
FIRST_ATTEMPT_KEY = "1"
MAX_ROLE_KEYS = 10
MAX_DISTINCT_MODELS = 8
KNOWN_ROLES = frozenset({"architect", "worker", "reviewer", "service", "qa", "report"})
COMMON_OPTION_KEYS = frozenset({"fresh", "think", "max_polls", "poll_seconds", "max_retries"})
QA_OPTION_KEYS = COMMON_OPTION_KEYS | {"mode", "max_minutes", "max_cost_usd"}
QA_MODES = ("off", "final", "ui_tasks", "nightly")
QA_DEFAULT_OPTIONS: dict[str, Any] = {"mode": "off", "max_minutes": 20, "max_cost_usd": 1}
QA_LIMIT_KEYS = ("max_minutes", "max_cost_usd")
_ATTEMPT_KEY_PATTERN = re.compile(r"[1-9][0-9]*")


@dataclass(frozen=True)
class RolesReport:
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class _DuplicateKeyError(ValueError):
    pass


def split_model(model: str) -> tuple[str, str]:
    provider, _, name = model.partition(MODEL_SEPARATOR)
    return provider, name


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def validate_roles_json(text: str, providers: Collection[str]) -> RolesReport:
    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except _DuplicateKeyError as exc:
        return RolesReport(errors=(f"roles_json has a duplicate key {str(exc)!r}",))
    except ValueError as exc:
        return RolesReport(errors=(f"roles_json is not valid JSON: {exc}",))
    return validate_roles(parsed, providers)


def validate_roles(roles: Any, providers: Collection[str]) -> RolesReport:
    if not isinstance(roles, dict):
        return RolesReport(errors=("roles_json must be a JSON object",))
    errors: list[str] = []
    warnings: list[str] = []
    models: set[str] = set()
    for role, value in roles.items():
        if role not in KNOWN_ROLES:
            warnings.append(f"role {role!r} is not a known role")
        errors.extend(_check_role(role, value, providers))
        models.update(_role_models(value))
    if len(models) > MAX_DISTINCT_MODELS:
        warnings.append(
            f"roles_json uses {len(models)} distinct models, more than {MAX_DISTINCT_MODELS}"
        )
    return RolesReport(errors=tuple(errors), warnings=tuple(warnings))


def _check_role(role: str, value: Any, providers: Collection[str]) -> list[str]:
    if isinstance(value, str):
        return _check_model(role, "model", value, providers)
    if not isinstance(value, dict):
        return [f"role {role!r}: value must be a model string or an object of attempt keys"]
    errors: list[str] = []
    if len(value) > MAX_ROLE_KEYS:
        errors.append(f"role {role!r}: {len(value)} keys, at most {MAX_ROLE_KEYS} allowed")
    if OPTIONS_KEY in value:
        errors.extend(_check_options(role, value[OPTIONS_KEY]))
    errors.extend(_check_attempts(role, value, providers))
    return errors


def _check_attempts(role: str, attempts: dict[str, Any], providers: Collection[str]) -> list[str]:
    attempt_keys = [key for key in attempts if key != OPTIONS_KEY]
    errors: list[str] = []
    if attempt_keys and attempt_keys[0] != FIRST_ATTEMPT_KEY:
        errors.append(
            f"role {role!r}: first attempt key must be {FIRST_ATTEMPT_KEY!r}, "
            f"found {attempt_keys[0]!r}"
        )
    previous = 0
    for key in attempt_keys:
        if not _ATTEMPT_KEY_PATTERN.fullmatch(key):
            errors.append(f"role {role!r}: attempt key {key!r} must be a positive integer")
        elif int(key) <= previous:
            errors.append(f"role {role!r}: attempt key {key!r} is not greater than {previous}")
        else:
            previous = int(key)
        errors.extend(_check_model(role, f"attempt {key!r}", attempts[key], providers))
    return errors


def _check_model(role: str, element: str, model: Any, providers: Collection[str]) -> list[str]:
    if not isinstance(model, str):
        return [f"role {role!r}, {element}: model must be a string 'provider/model'"]
    provider, name = split_model(model)
    if not name:
        return [f"role {role!r}, {element}: model {model!r} has no model name after the provider"]
    if provider not in providers:
        return [f"role {role!r}, {element}: unknown provider {provider!r}"]
    return []


def _check_options(role: str, options: Any) -> list[str]:
    if not isinstance(options, dict):
        return [f"role {role!r}: {OPTIONS_KEY!r} must be an object"]
    allowed = QA_OPTION_KEYS if role == "qa" else COMMON_OPTION_KEYS
    errors = [f"role {role!r}: unknown option {key!r}" for key in options if key not in allowed]
    if role == "qa":
        errors.extend(_check_qa_options(role, options))
    return errors


def _check_qa_options(role: str, options: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "mode" in options and options["mode"] not in QA_MODES:
        errors.append(
            f"role {role!r}: option 'mode' {options['mode']!r} must be one of {', '.join(QA_MODES)}"
        )
    for key in QA_LIMIT_KEYS:
        if key in options and not _is_positive_number(options[key]):
            errors.append(f"role {role!r}: option {key!r} must be a positive number")
    return errors


def _is_positive_number(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _role_models(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            model for key, model in value.items() if key != OPTIONS_KEY and isinstance(model, str)
        ]
    return []


FRESH_KEY = "fresh"
FRESH_AUTO = "auto"
LEGACY_OPTION_KEYS = ("think", "max_polls", "poll_seconds", "max_retries")
FALLBACK_ROLES = {"qa": ("qa", "reviewer"), "report": ("report", "worker")}
DEFAULT_MODELS = {"qa": "anthropic/sonnet"}


@dataclass(frozen=True)
class ResolvedRole:
    provider: str
    model: str
    think: str | None
    fresh: bool
    options: dict[str, Any]


def merge(
    global_roles: Mapping[str, Any] | None, mission_roles: Mapping[str, Any] | None
) -> dict[str, Any]:
    merged = dict(global_roles or {})
    merged.update(mission_roles or {})
    return merged


def legacy_role(columns: Mapping[str, Any], role: str) -> dict[str, Any]:
    value: dict[str, Any] = {}
    provider = columns.get(f"{role}_provider")
    model = columns.get(f"{role}_model")
    if provider and model:
        value[FIRST_ATTEMPT_KEY] = f"{provider}{MODEL_SEPARATOR}{model}"
    options = {key: columns.get(f"{role}_{key}") for key in LEGACY_OPTION_KEYS}
    value[OPTIONS_KEY] = {key: option for key, option in options.items() if option is not None}
    return value


def resolve(
    roles: Mapping[str, Any], role: str, attempt: int, legacy: Mapping[str, Any]
) -> ResolvedRole:
    model, picked = _pick(roles, role, attempt, legacy)
    defaults = QA_DEFAULT_OPTIONS if role == "qa" else {}
    options = {FRESH_KEY: FRESH_AUTO, **defaults, **picked}
    provider, name = split_model(model)
    fresh_option = options[FRESH_KEY]
    if fresh_option == FRESH_AUTO:
        fresh = attempt > 1 and _pick(roles, role, attempt - 1, legacy)[0] != model
    elif isinstance(fresh_option, bool):
        fresh = fresh_option
    else:
        raise ValueError(f"role {role!r}: {FRESH_KEY!r} must be {FRESH_AUTO!r}, true or false")
    return ResolvedRole(
        provider=provider,
        model=name,
        think=options.get("think"),
        fresh=fresh,
        options=options,
    )


def _pick(
    roles: Mapping[str, Any], role: str, attempt: int, legacy: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    chain = FALLBACK_ROLES.get(role, (role,))
    legacy_values = {name: legacy_role(legacy, name) for name in chain}
    legacy_options: dict[str, Any] = {}
    for name in chain:
        legacy_options = {**legacy_values[name][OPTIONS_KEY], **legacy_options}
    for name in chain:
        model = _model_for_attempt(roles.get(name), attempt)
        if model is not None:
            return model, {**legacy_options, **_options_of(roles[name])}
    for name in chain:
        model = _model_for_attempt(legacy_values[name], attempt)
        if model is not None:
            return model, legacy_options
    if role in DEFAULT_MODELS:
        return DEFAULT_MODELS[role], legacy_options
    raise ValueError(f"no model configured for role {role!r} on attempt {attempt}")


def _model_for_attempt(value: Any, attempt: int) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    keys = [
        key
        for key in value
        if key != OPTIONS_KEY and _ATTEMPT_KEY_PATTERN.fullmatch(key) and int(key) <= attempt
    ]
    if not keys:
        return None
    model = value[max(keys, key=int)]
    return model if isinstance(model, str) else None


def _options_of(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get(OPTIONS_KEY), dict):
        return dict(value[OPTIONS_KEY])
    return {}
