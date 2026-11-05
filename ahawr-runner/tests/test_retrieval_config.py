from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ahawr_runner.retrieval_config import flatten, validate

CASES: dict[str, Any] = json.loads(
    (Path(__file__).parent / "retrieval_cases.json").read_text(encoding="utf-8")
)

TODAYS_CONSTANTS: dict[str, Any] = {
    "retrieval_enabled": False,
    "retrieval_url": "http://ahawr-retrieval:8500",
    "retrieval_corpora_json": [],
    "retrieval_timeout_ms": 120000,
    "retrieval_worker_max_tokens": 6000,
    "retrieval_reviewer_max_tokens": 5000,
    "retrieval_label": "",
}


@pytest.mark.parametrize("case", CASES["parse"], ids=lambda case: case["name"])
def test_shared_parse_case(case: dict[str, Any]) -> None:
    expect = case["expect"]
    errors = validate(case["retrieval_json"])
    if "errors" in expect:
        assert errors == expect["errors"]
    for message in expect.get("errors_contain", []):
        assert any(message in error for error in errors)
    if errors:
        with pytest.raises(ValueError):
            flatten(case["retrieval_json"], case["legacy"])
        return
    flat = flatten(case["retrieval_json"], case["legacy"])
    if expect.get("flatten_equals") == "legacy_constants":
        assert flat == CASES["legacy_constants"]
    for key, value in expect.get("flatten_partial", {}).items():
        assert flat[key] == value


@pytest.mark.parametrize("raw", [None, ""])
def test_golden_empty_retrieval_json_flattens_to_todays_constants(raw: str | None) -> None:
    assert flatten(raw, {}) == TODAYS_CONSTANTS
