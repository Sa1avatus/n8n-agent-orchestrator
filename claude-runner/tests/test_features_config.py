from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from claude_runner.features_config import flatten, parse_features, validate_features_json

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
