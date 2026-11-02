from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from claude_runner.board_engine import (
    ENGINE_DEFAULTS,
    LEGACY_ERROR_RULES,
    RECOMMENDED_ERROR_RULES,
    flatten,
    match_error_rule,
    validate_engine_json,
)

CASES: dict[str, Any] = json.loads(
    (Path(__file__).parent / "engine_cases.json").read_text(encoding="utf-8")
)
LEGACY: dict[str, Any] = CASES["legacy_constants"]


def test_defaults_are_the_legacy_constants() -> None:
    assert ENGINE_DEFAULTS == LEGACY


@pytest.mark.parametrize("item", CASES["parse"], ids=[item["name"] for item in CASES["parse"]])
def test_parse_case(item: dict[str, Any]) -> None:
    errors = validate_engine_json(item["engine_json"])
    expect = item["expect"]
    if "errors" in expect:
        assert list(errors) == expect["errors"]
    for message in expect.get("errors_contain", []):
        assert any(message in error for error in errors), (message, errors)
    if errors:
        with pytest.raises(ValueError):
            flatten(item["engine_json"], item["legacy"])
        return
    flat = flatten(item["engine_json"], item["legacy"])
    if expect.get("flatten_equals") == "legacy_constants":
        assert flat == LEGACY
    for key, value in expect.get("flatten_partial", {}).items():
        assert flat[key] == value, key


@pytest.mark.parametrize(
    "item", CASES["flatten_errors"], ids=[item["name"] for item in CASES["flatten_errors"]]
)
def test_flatten_error_case(item: dict[str, Any]) -> None:
    assert validate_engine_json(item["engine_json"]) == ()
    if "error_contains" in item:
        with pytest.raises(ValueError, match=item["error_contains"]):
            flatten(item["engine_json"], item["legacy"])
    for key, value in item.get("flatten_partial", {}).items():
        assert flatten(item["engine_json"], item["legacy"])[key] == value, key


def test_flatten_returns_independent_default_lists() -> None:
    flatten(None, {})["retry_delays"].append(999)
    assert flatten(None, {})["retry_delays"] == LEGACY["retry_delays"]


@pytest.mark.parametrize(
    "item", CASES["error_rule_cases"], ids=[item["name"] for item in CASES["error_rule_cases"]]
)
def test_error_rule_case(item: dict[str, Any]) -> None:
    rules = RECOMMENDED_ERROR_RULES if item["rules"] == "recommended" else item["rules"]
    error = item["error"]
    rule = match_error_rule(rules, error["http"], error["code"], error["text"])
    expect = item["expect"]
    if expect["rule_id"] is None:
        assert rule is None
        return
    assert rule is not None
    assert rule["id"] == expect["rule_id"]
    assert rule["action"] == expect["action"]
    assert rule["retries"] == expect["retries"]


def test_presets_pass_validation() -> None:
    assert validate_engine_json(json.dumps({"error_rules": LEGACY_ERROR_RULES})) == ()
    assert validate_engine_json(json.dumps({"error_rules": RECOMMENDED_ERROR_RULES})) == ()


def test_presets_equal_shared_fixture() -> None:
    presets = json.loads(
        (Path(__file__).parent / "error_rule_presets.json").read_text(encoding="utf-8")
    )
    assert presets["legacy"] == LEGACY_ERROR_RULES
    assert presets["recommended"] == RECOMMENDED_ERROR_RULES
