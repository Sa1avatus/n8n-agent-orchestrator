from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

FEATURE_DEFAULTS: dict[str, dict[str, Any]] = {
    "plan_no_truncate": {"enabled": False, "max_tasks": 15, "max_tasks_hard": 30},
    "adaptive_split": {
        "enabled": False,
        "split_steps": 150,
        "max_splits": 6,
        "max_total_tasks": 40,
    },
    "run_hygiene": {"enabled": False},
}
PARAM_MINIMUMS: dict[str, int] = {
    "max_tasks": 1,
    "max_tasks_hard": 1,
    "split_steps": 1,
    "max_splits": 0,
    "max_total_tasks": 1,
}


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in JSON")


def _is_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


def _check_parameter(
    column: str,
    feature: str,
    param: str,
    value: Any,
    accepted: dict[str, Any],
    errors: list[str],
) -> None:
    where = f"{column}: {feature}.{param}"
    if param == "enabled":
        if isinstance(value, bool):
            accepted[param] = value
        else:
            errors.append(f"{where} must be true or false, got {json.dumps(value)}")
        return
    if _is_integer(value) and value >= PARAM_MINIMUMS[param]:
        accepted[param] = int(value)
    else:
        errors.append(
            f"{where} must be an integer >= {PARAM_MINIMUMS[param]}, got {json.dumps(value)}"
        )


def _check_feature(
    column: str, feature: str, body: dict[str, Any], errors: list[str]
) -> dict[str, Any]:
    accepted: dict[str, Any] = {}
    for param, value in body.items():
        if param not in FEATURE_DEFAULTS[feature]:
            errors.append(f'{column}: unknown parameter "{param}" in feature "{feature}"')
        else:
            _check_parameter(column, feature, param, value, accepted, errors)
    return accepted


def _parse_text(text: str | None, column: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    source = (text or "").strip()
    if not source:
        return {}
    try:
        parsed = json.loads(source, parse_constant=_reject_constant)
    except ValueError as exc:
        errors.append(f"{column}: not valid JSON ({exc})")
        return {}
    if not isinstance(parsed, dict):
        errors.append(f"{column}: must be a JSON object")
        return {}
    sparse: dict[str, dict[str, Any]] = {}
    for feature, body in parsed.items():
        if feature not in FEATURE_DEFAULTS:
            errors.append(f'{column}: unknown feature "{feature}"')
        elif not isinstance(body, dict):
            errors.append(f'{column}: feature "{feature}" must be an object')
        else:
            sparse[feature] = _check_feature(column, feature, body, errors)
    return sparse


def _resolve_features(
    global_features: Mapping[str, Mapping[str, Any]],
    mission_features: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        feature: {
            **defaults,
            **global_features.get(feature, {}),
            **mission_features.get(feature, {}),
        }
        for feature, defaults in FEATURE_DEFAULTS.items()
    }


def _check_limits(features: Mapping[str, Mapping[str, Any]], errors: list[str]) -> None:
    plan = features["plan_no_truncate"]
    if plan["max_tasks_hard"] < plan["max_tasks"]:
        errors.append(
            "plan_no_truncate.max_tasks_hard must be at least plan_no_truncate.max_tasks "
            f"({plan['max_tasks']}), got {plan['max_tasks_hard']}"
        )


def parse_features(
    features_json: str | None, mission_features_json: str | None
) -> tuple[dict[str, dict[str, Any]] | None, list[str]]:
    errors: list[str] = []
    global_features = _parse_text(features_json, "features_json", errors)
    mission_features = _parse_text(mission_features_json, "mission_features_json", errors)
    features = _resolve_features(global_features, mission_features)
    _check_limits(features, errors)
    if errors:
        return None, errors
    return features, errors


def validate_features_json(
    features_json: str | None, mission_features_json: str | None
) -> tuple[str, ...]:
    _, errors = parse_features(features_json, mission_features_json)
    return tuple(errors)


def flatten(features: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    plan = features["plan_no_truncate"]
    split = features["adaptive_split"]
    return {
        "plan_no_truncate": int(plan["enabled"]),
        "max_tasks": plan["max_tasks"],
        "max_tasks_hard": plan["max_tasks_hard"],
        "adaptive_split": int(split["enabled"]),
        "split_steps": split["split_steps"],
        "max_splits": split["max_splits"],
        "max_total_tasks": split["max_total_tasks"],
        "run_hygiene": int(features["run_hygiene"]["enabled"]),
    }
