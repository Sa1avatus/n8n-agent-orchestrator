from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

from claude_runner.features_config import _is_integer, _reject_constant

DEFAULTS: dict[str, Any] = {
    "retrieval_enabled": False,
    "retrieval_url": "http://ahawr-retrieval:8500",
    "retrieval_corpora_json": [],
    "retrieval_timeout_ms": 120000,
    "retrieval_worker_max_tokens": 6000,
    "retrieval_reviewer_max_tokens": 5000,
    "retrieval_label": "",
}
JSON_KEYS: dict[str, str] = {
    "enabled": "retrieval_enabled",
    "url": "retrieval_url",
    "corpora": "retrieval_corpora_json",
    "timeout_ms": "retrieval_timeout_ms",
    "worker_max_tokens": "retrieval_worker_max_tokens",
    "reviewer_max_tokens": "retrieval_reviewer_max_tokens",
    "label": "retrieval_label",
}
INTEGER_MINIMUMS: dict[str, int] = {
    "timeout_ms": 1000,
    "worker_max_tokens": 100,
    "reviewer_max_tokens": 100,
}
EXPECTED: dict[str, str] = {
    "enabled": "true or false",
    "url": "a non-empty string",
    "corpora": "an array of non-empty strings",
    "label": "a string",
}


def _is_valid(key: str, value: Any) -> bool:
    if key == "enabled":
        return isinstance(value, bool)
    if key in INTEGER_MINIMUMS:
        return _is_integer(value) and value >= INTEGER_MINIMUMS[key]
    if key == "corpora":
        return isinstance(value, list) and all(
            isinstance(item, str) and item.strip() != "" for item in value
        )
    if key == "url":
        return isinstance(value, str) and value.strip() != ""
    return isinstance(value, str)


def _expected(key: str) -> str:
    if key in INTEGER_MINIMUMS:
        return f"an integer >= {INTEGER_MINIMUMS[key]}"
    return EXPECTED[key]


def _check_key(key: str, value: Any, accepted: dict[str, Any], errors: list[str]) -> None:
    if key not in JSON_KEYS:
        errors.append(f'retrieval_json: unknown key "{key}"')
    elif _is_valid(key, value):
        accepted[JSON_KEYS[key]] = value.strip() if key == "label" else value
    else:
        errors.append(f"retrieval_json: {key} must be {_expected(key)}, got {json.dumps(value)}")


def parse(raw: str | Mapping[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    if raw is None:
        return {}, []
    given: Any = raw
    if isinstance(raw, str):
        if raw.strip() == "":
            return {}, []
        try:
            given = json.loads(raw, parse_constant=_reject_constant)
        except ValueError:
            return {}, ["retrieval_json: not valid JSON"]
    if not isinstance(given, Mapping):
        return {}, ["retrieval_json: must be a JSON object"]
    accepted: dict[str, Any] = {}
    errors: list[str] = []
    for key, value in given.items():
        _check_key(key, value, accepted, errors)
    return accepted, errors


def validate(raw: str | Mapping[str, Any] | None) -> list[str]:
    return parse(raw)[1]


def _is_set(value: Any) -> bool:
    return value is not None and value != ""


def flatten(raw: str | Mapping[str, Any] | None, legacy: Mapping[str, Any]) -> dict[str, Any]:
    given, errors = parse(raw)
    if errors:
        raise ValueError("; ".join(errors))
    flat: dict[str, Any] = {}
    for name, default in DEFAULTS.items():
        if name in given:
            flat[name] = given[name]
        elif _is_set(legacy.get(name)):
            flat[name] = legacy[name]
        else:
            flat[name] = copy.deepcopy(default)
    return flat
