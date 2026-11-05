from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ahawr_runner.features_config import (
    _reject_constant,
    flatten,
    parse_features,
    validate_features_json,
)

CASES: dict[str, Any] = json.loads(
    (Path(__file__).parent / "features_cases.json").read_text(encoding="utf-8")
)
LEGACY: dict[str, Any] = CASES["legacy_constants"]


@pytest.mark.parametrize("item", CASES["parse"], ids=[item["name"] for item in CASES["parse"]])
def test_parse_case(item: dict[str, Any]) -> None:
    features, errors = parse_features(item["features_json"], item["mission_features_json"])
    assert validate_features_json(item["features_json"], item["mission_features_json"]) == tuple(
        errors
    )
    expect = item["expect"]
    if "errors" in expect:
        assert errors == expect["errors"]
    for message in expect.get("errors_contain", []):
        assert message in errors
    if expect.get("flatten_equals") == "legacy_constants":
        assert features is not None
        assert flatten(features) == LEGACY
    flat = flatten(features) if features is not None else {}
    for key, value in expect.get("flatten_partial", {}).items():
        assert flat[key] == value


def test_golden_empty_or_missing_flattens_byte_for_byte_to_legacy() -> None:
    legacy_text = json.dumps(LEGACY, separators=(",", ":"))
    for text in ["", None]:
        features, errors = parse_features(text, text)
        assert errors == []
        assert features is not None
        assert json.dumps(flatten(features), separators=(",", ":")) == legacy_text


def test_non_standard_json_constants_are_rejected() -> None:
    features, errors = parse_features('{"run_hygiene":{"enabled":NaN}}', "")
    assert features is None
    assert errors == ['features_json: not valid JSON (NaN is not allowed in JSON)']


def test_reject_constant_refuses_every_non_standard_json_literal() -> None:
    """The hook the features parser passes to json.loads rejects NaN, Infinity and
    -Infinity by name; null/true/false are parsed normally, they are not constants."""
    for literal in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError, match=f"{literal} is not allowed in JSON"):
            _reject_constant(literal)
    assert json.loads("null", parse_constant=_reject_constant) is None
    assert json.loads("true", parse_constant=_reject_constant) is True


def test_features_constants_are_rejected_in_both_columns() -> None:
    """The same hook guards features_json and mission_features_json alike, each error
    naming the column it came from."""
    features, errors = parse_features('{"run_hygiene": {"enabled": Infinity}}', "")
    assert features is None
    assert errors == ["features_json: not valid JSON (Infinity is not allowed in JSON)"]

    features, errors = parse_features("", '{"run_hygiene": {"enabled": -Infinity}}')
    assert features is None
    assert errors == [
        "mission_features_json: not valid JSON (-Infinity is not allowed in JSON)"
    ]


def test_event_schema_version_is_a_flag_with_no_parameters() -> None:
    """The new feature is known, defaults off, and adds no legacy constant to flatten."""
    features, errors = parse_features('{"event_schema_version": {"enabled": true}}', "")
    assert errors == []
    assert features is not None
    assert features["event_schema_version"]["enabled"] is True
    assert flatten(features) == LEGACY
    mission_only, errors = parse_features("", '{"event_schema_version": {"enabled": true}}')
    assert errors == []
    assert mission_only is not None
    assert mission_only["event_schema_version"]["enabled"] is True
    bad, errors = parse_features('{"event_schema_version": {"enabled": "yes"}}', "")
    assert bad is None
    assert errors == [
        'features_json: event_schema_version.enabled must be true or false, got "yes"'
    ]
    bad, errors = parse_features('{"event_schema_version": {"max_tasks": 3}}', "")
    assert bad is None
    assert errors == [
        'features_json: unknown parameter "max_tasks" in feature "event_schema_version"'
    ]
