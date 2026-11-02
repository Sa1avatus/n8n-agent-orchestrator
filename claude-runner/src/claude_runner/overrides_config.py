from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

from claude_runner.features_config import _is_integer, _reject_constant

PREFIX = "overrides_json"
RETRIEVAL_SECTION = "retrieval"
CORPORA_NAME = "retrieval_corpora"
SECTIONS: tuple[str, ...] = ("roles", "features", RETRIEVAL_SECTION)
RETRIEVAL_NAMES: dict[str, str] = {
    "enabled": "retrieval_enabled",
    "url": "retrieval_url",
    "corpora": CORPORA_NAME,
    "timeout_ms": "retrieval_timeout_ms",
    "worker_max_tokens": "retrieval_worker_max_tokens",
    "reviewer_max_tokens": "retrieval_reviewer_max_tokens",
    "label": "retrieval_label",
}
DEFAULTS: dict[str, Any] = {
    "retrieval_enabled": False,
    "retrieval_url": "http://ahawr-retrieval:8500",
    "retrieval_corpora": [],
    "retrieval_timeout_ms": 120000,
    "retrieval_worker_max_tokens": 6000,
    "retrieval_reviewer_max_tokens": 5000,
    "retrieval_label": "",
}
LEGACY_COLUMNS: dict[str, str] = {name: name for name in DEFAULTS} | {
    CORPORA_NAME: "retrieval_corpora_json",
}
INTEGER_MINIMUMS: dict[str, int] = {
    "timeout_ms": 1000,
    "worker_max_tokens": 100,
    "reviewer_max_tokens": 100,
}


def _is_set(value: Any) -> bool:
    return value is not None and value != ""


def _load_object(overrides_json: str | None, errors: list[str]) -> Mapping[str, Any]:
    if overrides_json is None or overrides_json.strip() == "":
        return {}
    try:
        given: Any = json.loads(overrides_json, parse_constant=_reject_constant)
    except ValueError:
        errors.append(f"{PREFIX}: invalid JSON")
        return {}
    if not isinstance(given, dict):
        errors.append(f"{PREFIX}: must be a JSON object")
        return {}
    return given


def _field_errors(key: str, value: Any) -> list[str]:
    name = f"{RETRIEVAL_SECTION}.{key}"
    if key in INTEGER_MINIMUMS:
        minimum = INTEGER_MINIMUMS[key]
        if _is_integer(value) and value >= minimum:
            return []
        return [f"{name} must be an integer >= {minimum}, got {json.dumps(value)}"]
    if key == "corpora":
        if not isinstance(value, list):
            return [f"{name} must be an array, got {json.dumps(value)}"]
        return [
            f"{name}[{index}] must be a string, got {json.dumps(item)}"
            for index, item in enumerate(value)
            if not isinstance(item, str)
        ]
    if key == "enabled":
        valid, expected = isinstance(value, bool), "true or false"
    elif key == "url":
        valid, expected = isinstance(value, str) and value.strip() != "", "a non-empty string"
    else:
        valid, expected = isinstance(value, str), "a string"
    return [] if valid else [f"{name} must be {expected}, got {json.dumps(value)}"]


def _check_retrieval(given: Mapping[str, Any], accepted: dict[str, Any], errors: list[str]) -> None:
    for key, value in given.items():
        if key not in RETRIEVAL_NAMES:
            errors.append(f'{PREFIX}: unknown key "{RETRIEVAL_SECTION}.{key}"')
            continue
        field_errors = _field_errors(key, value)
        errors.extend(f"{PREFIX}: {message}" for message in field_errors)
        if not field_errors:
            accepted[RETRIEVAL_NAMES[key]] = value


def _parse(overrides_json: str | None) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    given = _load_object(overrides_json, errors)
    accepted: dict[str, Any] = {}
    for section, value in given.items():
        if section not in SECTIONS:
            errors.append(f'{PREFIX}: unknown key "{section}"')
        elif not isinstance(value, dict):
            errors.append(f"{PREFIX}: {section} must be an object")
        elif section == RETRIEVAL_SECTION:
            _check_retrieval(value, accepted, errors)
    return accepted, errors


def validate(overrides_json: str | None) -> list[str]:
    return _parse(overrides_json)[1]


def _legacy_value(name: str, legacy_row: Mapping[str, Any]) -> Any:
    value: Any = legacy_row.get(LEGACY_COLUMNS[name])
    if not _is_set(value):
        return None
    if name == CORPORA_NAME:
        return json.loads(value)
    return value


def resolve(overrides_json: str | None, legacy_row: Mapping[str, Any]) -> dict[str, Any]:
    accepted, errors = _parse(overrides_json)
    if errors:
        raise ValueError("; ".join(errors))
    return {
        name: accepted[name] if name in accepted else _legacy_value(name, legacy_row)
        for name in DEFAULTS
    }


def flatten(overrides_json: str | None, legacy_row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: copy.deepcopy(DEFAULTS[name]) if value is None else value
        for name, value in resolve(overrides_json, legacy_row).items()
    }


def resolve_state_key(mission_id: str, state_namespace: str | None) -> str:
    return state_namespace or mission_id
