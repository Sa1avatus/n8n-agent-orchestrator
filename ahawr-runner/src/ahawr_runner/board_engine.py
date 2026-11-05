from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping
from typing import Any

ENGINE_COLUMN = "engine_json"
ENGINE_DEFAULTS: dict[str, Any] = {
    "max_attempts_per_task": 5,
    "retry_delays": [15, 30, 60, 120, 300],
    "runner_url": "http://ahawr-runner:8700",
    "run_manager_workflow_id": "nhjwX1G7FiVTO2Ah",
    "telegram_chat_id": "",
}
LEGACY_COLUMNS: dict[str, str] = {
    "max_attempts_per_task": "max_attempts_per_task",
    "retry_delays": "retry_delays_json",
    "runner_url": "runner_url",
    "run_manager_workflow_id": "run_manager_workflow_id",
    "telegram_chat_id": "telegram_chat_id",
}
ENGINE_KEYS = (*ENGINE_DEFAULTS, "error_rules")
MIN_MAX_ATTEMPTS = 1
MAX_ERROR_RULES = 40
MAX_RULE_RETRIES = 5
ERROR_ACTIONS = ("compact", "fail", "failover", "retry")
ERROR_RULE_KEYS = frozenset({"id", "match", "action", "retries", "note"})
ERROR_MATCH_KEYS = frozenset({"http", "words", "codes"})
# shortcut: legacy rules use the worker default 3, not the role's max_retries,
# upgrade when a rule can name a role's retry count
LEGACY_RULE_RETRIES = 3
LEGACY_ERROR_RULES: list[dict[str, Any]] = [
    {
        "id": "legacy-context-code",
        "match": {"codes": ["context_window"]},
        "action": "compact",
        "retries": LEGACY_RULE_RETRIES,
    },
    {
        "id": "legacy-context-words",
        "match": {
            "words": [
                "context window exceeded",
                "contextwindow exceeded",
                "context windowexceeded",
                "contextwindowexceeded",
                "exceeds the available context",
                "context length",
                "prompt is too long",
                "maximum context",
            ]
        },
        "action": "compact",
        "retries": LEGACY_RULE_RETRIES,
    },
    {
        "id": "legacy-transient-http",
        "match": {"http": [400, 408, 409, 425, 429, 500, 502, 503, 504, 529]},
        "action": "retry",
        "retries": LEGACY_RULE_RETRIES,
    },
    {
        "id": "legacy-transient-words",
        "match": {
            "words": [
                "temporarily overloaded",
                "upstream error",
                "upstream request failed",
                "service unavailable",
                "overloaded",
                "timeout",
                "timed out",
                "connection reset",
                "connection refused",
                "rate limit",
                "too many requests",
                "no longer available",
                "no provider serves",
                "model not found",
            ]
        },
        "action": "retry",
        "retries": LEGACY_RULE_RETRIES,
    },
]
RECOMMENDED_ERROR_RULES: list[dict[str, Any]] = [
    {
        "id": "weekly-limit-text",
        "match": {"words": ["weekly limit"]},
        "action": "retry",
        "retries": 2,
    },
    {
        "id": "rate-limit-429",
        "match": {"http": [429], "words": ["rate limit", "too many requests"]},
        "action": "retry",
        "retries": 2,
    },
    {
        "id": "transient-5xx",
        "match": {
            "http": [408, 409, 425, 500, 502, 503, 504, 529],
            "words": ["timeout", "overloaded", "upstream", "connection reset"],
        },
        "action": "retry",
        "retries": 2,
    },
    {
        "id": "model-gone",
        "match": {
            "http": [400, 404],
            "words": [
                "no longer available",
                "no provider serves",
                "model not found",
                "does not exist",
            ],
        },
        "action": "failover",
        "retries": 0,
    },
    {
        "id": "credits-402",
        "match": {
            "http": [402],
            "words": ["insufficient credits", "credit balance", "quota exceeded", "usage limit"],
        },
        "action": "failover",
        "retries": 0,
    },
    {
        "id": "auth-401-403",
        "match": {"http": [401, 403], "words": ["invalid api key", "expired"]},
        "action": "failover",
        "retries": 0,
    },
    {
        "id": "context-413",
        "match": {"http": [413], "words": ["context length"]},
        "action": "compact",
        "retries": 2,
    },
    {"id": "other-400", "match": {"http": [400]}, "action": "retry", "retries": 2},
    {"id": "else-fail", "match": {}, "action": "fail", "retries": 0},
]


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in JSON")


def _attempts(value: Any, where: str) -> Any:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value < MIN_MAX_ATTEMPTS
    ):
        raise ValueError(f"{where} must be a number of at least {MIN_MAX_ATTEMPTS}, got {value!r}")
    return value


def _delay(element: Any, where: str) -> Any:
    number = element
    if isinstance(element, str):
        try:
            number = float(element)
        except ValueError:
            raise ValueError(f"{where} must hold numbers >= 0, got {element!r}") from None
    if (
        isinstance(number, bool)
        or not isinstance(number, int | float)
        or not math.isfinite(number)
        or number < 0
    ):
        raise ValueError(f"{where} must hold numbers >= 0, got {element!r}")
    return number


def _delays(value: Any, where: str) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = json.loads(text, parse_constant=_reject_constant)
        except ValueError as exc:
            raise ValueError(f"{where} is not valid JSON ({exc})") from None
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a JSON array of delays in seconds, got {value!r}")
    return [_delay(element, where) for element in value]


def _text(key: str, value: Any, where: str) -> str | None:
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a string, got {value!r}")
    text = value.strip()
    if key == "runner_url":
        text = text.rstrip("/")
    return text or None


def _all_of(value: Any, kind: type) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, kind) and not isinstance(item, bool) for item in value
    )


def _match(match: Any, where: str) -> None:
    if not isinstance(match, dict):
        raise ValueError(f"{where} must be a JSON object, got {match!r}")
    for key in match:
        if key not in ERROR_MATCH_KEYS:
            raise ValueError(f'{where}: unknown key "{key}"')
    if "http" in match and not _all_of(match["http"], int):
        raise ValueError(f"{where}.http must be a list of integers, got {match['http']!r}")
    for key in ("words", "codes"):
        if key in match and not _all_of(match[key], str):
            raise ValueError(f"{where}.{key} must be a list of strings, got {match[key]!r}")


def _rule(rule: Any, where: str) -> None:
    if not isinstance(rule, dict):
        raise ValueError(f"{where} must be a JSON object, got {rule!r}")
    for key in rule:
        if key not in ERROR_RULE_KEYS:
            raise ValueError(f'{where}: unknown key "{key}"')
    rule_id = rule.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise ValueError(f"{where}.id must be a non-empty string, got {rule_id!r}")
    action = rule.get("action")
    if action not in ERROR_ACTIONS:
        raise ValueError(
            f"{where}.action must be one of {', '.join(ERROR_ACTIONS)}, got {action!r}"
        )
    retries = rule.get("retries")
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= MAX_RULE_RETRIES
    ):
        raise ValueError(
            f"{where}.retries must be an integer from 0 to {MAX_RULE_RETRIES}, got {retries!r}"
        )
    if "note" in rule and not isinstance(rule["note"], str):
        raise ValueError(f"{where}.note must be a string, got {rule['note']!r}")
    _match(rule.get("match", {}), f"{where}.match")


def _rules(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a JSON array of rules, got {value!r}")
    if len(value) > MAX_ERROR_RULES:
        raise ValueError(f"{where} must hold at most {MAX_ERROR_RULES} rules, got {len(value)}")
    ids: set[str] = set()
    for index, rule in enumerate(value):
        _rule(rule, f"{where}[{index}]")
        if rule["id"] in ids:
            raise ValueError(f'{where}: duplicate id "{rule["id"]}"')
        ids.add(rule["id"])
    return value


def _normalize(key: str, value: Any, where: str) -> Any:
    """Return the accepted value, None when the value counts as absent, or raise ValueError."""
    if value is None:
        return None
    if key == "retry_delays":
        return _delays(value, where)
    if key == "max_attempts_per_task":
        return _attempts(value, where)
    if key == "error_rules":
        return _rules(value, where)
    return _text(key, value, where)


def parse_engine(engine_json: str | None) -> tuple[dict[str, Any], list[str]]:
    source = (engine_json or "").strip()
    if not source:
        return {}, []
    try:
        parsed = json.loads(source, parse_constant=_reject_constant)
    except ValueError as exc:
        return {}, [f"{ENGINE_COLUMN} is not valid JSON ({exc})"]
    if not isinstance(parsed, dict):
        return {}, [f"{ENGINE_COLUMN} must be a JSON object"]
    accepted: dict[str, Any] = {}
    errors: list[str] = []
    for key, value in parsed.items():
        if key not in ENGINE_KEYS:
            errors.append(f'{ENGINE_COLUMN}: unknown key "{key}"')
            continue
        try:
            normalized = _normalize(key, value, f"{ENGINE_COLUMN}.{key}")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if normalized is not None:
            accepted[key] = normalized
    return accepted, errors


def validate_engine_json(engine_json: str | None) -> tuple[str, ...]:
    _, errors = parse_engine(engine_json)
    return tuple(errors)


def flatten(engine_json: str | None, legacy_row: Mapping[str, Any]) -> dict[str, Any]:
    accepted, errors = parse_engine(engine_json)
    if errors:
        raise ValueError("; ".join(errors))
    flat: dict[str, Any] = {}
    for key, default in ENGINE_DEFAULTS.items():
        value = accepted.get(key)
        if value is None:
            column = LEGACY_COLUMNS[key]
            value = _normalize(key, legacy_row.get(column), column)
        flat[key] = copy.deepcopy(default) if value is None else value
    return flat


def _rule_matches(
    match: dict[str, Any], http_code: int | None, failure_code: str, text: str
) -> bool:
    if match.get("http") and http_code not in match["http"]:
        return False
    if match.get("words") and not any(word.lower() in text for word in match["words"]):
        return False
    return not match.get("codes") or any(code.lower() in failure_code for code in match["codes"])


def match_error_rule(
    rules: list[dict[str, Any]] | None,
    http_code: int | None,
    failure_code: str | None,
    text: str | None,
) -> dict[str, Any] | None:
    lowered_code = (failure_code or "").lower()
    lowered_text = (text or "").lower()
    for rule in rules or LEGACY_ERROR_RULES:
        if _rule_matches(rule.get("match", {}), http_code, lowered_code, lowered_text):
            return rule
    return None
