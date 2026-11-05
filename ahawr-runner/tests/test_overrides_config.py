from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ahawr_runner.overrides_config import flatten, resolve_state_key, validate

CASES: dict[str, Any] = json.loads(
    (Path(__file__).parent / "overrides_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES["parse"], ids=lambda case: case["name"])
def test_shared_overrides_case(case: dict[str, Any]) -> None:
    expect = case["expect"]
    errors = validate(case["overrides_json"])
    if "errors" in expect:
        assert errors == expect["errors"]
    for message in expect.get("errors_contain", []):
        assert message in errors
    if "state_key" in expect:
        assert resolve_state_key(case["mission_id"], case["state_namespace"]) == expect["state_key"]
    if errors:
        with pytest.raises(ValueError):
            flatten(case["overrides_json"], case["legacy"])
        return
    flat = flatten(case["overrides_json"], case["legacy"])
    if expect.get("flatten_equals") == "legacy_constants":
        assert flat == CASES["legacy_constants"]
    for key, value in expect.get("flatten_partial", {}).items():
        assert flat[key] == value
