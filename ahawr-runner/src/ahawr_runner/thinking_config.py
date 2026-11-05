from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ahawr_runner.features_config import _is_integer, _reject_constant

DEFAULTS: dict[str, Any] = {
    "worker_think_policy": "",
    "worker_think_policy_from_task": 0,
    "worker_think_off_after_seconds": -1,
    "worker_think_off_after_steps": -1,
}
JSON_KEYS: dict[str, str] = {
    "from_task": "worker_think_policy_from_task",
    "off_after_seconds": "worker_think_off_after_seconds",
    "off_after_steps": "worker_think_off_after_steps",
}
JSON_MINIMUMS: dict[str, int] = {"from_task": 1, "off_after_seconds": 0, "off_after_steps": 0}
# the thinking profile catalog of a thinking_json is read by think_profiles.py,
# none of its keys reaches the Worker settings here
CATALOG_KEYS = ("profiles",)


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    return "number"


def _check_key(key: str, value: Any, accepted: dict[str, Any], errors: list[str]) -> None:
    if key in CATALOG_KEYS:
        return
    if key not in JSON_KEYS:
        errors.append(f'thinking_json: unknown key "{key}"')
    elif _is_integer(value) and value >= JSON_MINIMUMS[key]:
        accepted[JSON_KEYS[key]] = int(value)
    else:
        errors.append(
            f"thinking_json: {key} must be an integer >= {JSON_MINIMUMS[key]}, "
            f"got {json.dumps(value)}"
        )


def _parse(thinking_json: str | None, errors: list[str]) -> dict[str, Any]:
    source = (thinking_json or "").strip()
    if not source:
        return {}
    try:
        parsed = json.loads(source, parse_constant=_reject_constant)
    except ValueError:
        errors.append("thinking_json: not valid JSON")
        return {}
    if not isinstance(parsed, dict):
        errors.append(f"thinking_json: must be a JSON object, got {_json_type(parsed)}")
        return {}
    accepted: dict[str, Any] = {}
    for key, value in parsed.items():
        _check_key(key, value, accepted, errors)
    return accepted


def validate(thinking_json: str | None) -> list[str]:
    errors: list[str] = []
    _parse(thinking_json, errors)
    return errors


def _is_set(value: Any) -> bool:
    return value is not None and value != ""


def flatten(thinking_json: str | None, legacy: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    given = _parse(thinking_json, errors)
    if errors:
        raise ValueError("; ".join(errors))
    flat: dict[str, Any] = {}
    for name, default in DEFAULTS.items():
        if name in given:
            flat[name] = given[name]
        elif _is_set(legacy.get(name)):
            flat[name] = legacy[name]
        else:
            flat[name] = default
    return flat
