from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from claude_runner.provider_api_types import (
    API_TYPES,
    CAPABILITY_KEYS,
    check_public_base_url,
    effective_api_type,
    effective_capabilities,
    get_api_type,
    load_templates,
    route_for,
    worker_warning,
)

CASES_PATH = Path(__file__).with_name("provider_api_types_cases.json")
CASES: dict[str, Any] = json.loads(CASES_PATH.read_text(encoding="utf-8"))


def test_every_row_is_valid() -> None:
    for row in API_TYPES:
        assert row["id"]
        assert row["endpoint_path"].startswith("/")
        assert row["auth_style"] in {"x-api-key", "bearer", "none"}
        assert row["litellm_prefix"].endswith("/")
        assert set(row["capabilities"]) == CAPABILITY_KEYS


def test_ids_are_unique() -> None:
    ids = [row["id"] for row in API_TYPES]
    assert len(ids) == len(set(ids))


def test_every_non_anthropic_type_needs_a_translator() -> None:
    for row in API_TYPES:
        assert row["needs_translator"] is (row["id"] != "anthropic")


@pytest.mark.parametrize("case", CASES["route_cases"], ids=lambda case: case["api_type"])
def test_route_for_sends_only_non_anthropic_types_through_litellm(case: dict[str, str]) -> None:
    assert route_for(case["api_type"]) == case["expected"]


def test_unverified_rows_are_off_by_default() -> None:
    assert [row["id"] for row in API_TYPES if not row["verified"]] == ["openai-responses"]


def test_get_api_type_rejects_an_unknown_id() -> None:
    with pytest.raises(ValueError, match="unknown api_type"):
        get_api_type("no-such-type")


@pytest.mark.parametrize("case", CASES["effective_api_type_cases"])
def test_model_override_is_honoured(case: dict[str, Any]) -> None:
    assert effective_api_type(case["provider"], case["model"]) == case["expected"]


@pytest.mark.parametrize("case", CASES["effective_capabilities_cases"])
def test_capability_override_is_applied(case: dict[str, Any]) -> None:
    capabilities = effective_capabilities(case["api_type"], case["model_overrides"])
    for key, value in case["expected"].items():
        assert capabilities[key] == value


def test_unknown_capability_override_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown capability"):
        effective_capabilities("openai-chat", {"parallel_tool": False})


@pytest.mark.parametrize("case", CASES["worker_warning_cases"])
def test_tools_warning_only_when_tool_calls_are_missing(case: dict[str, Any]) -> None:
    assert worker_warning(case["capabilities"]) == case["expected"]


def test_module_table_equals_cases_table() -> None:
    assert [dict(row) for row in API_TYPES] == CASES["table"]


def test_every_template_references_an_existing_type() -> None:
    ids = {row["id"] for row in API_TYPES}
    assert {template["api_type"] for template in load_templates()} <= ids


def test_template_default_base_urls_are_public() -> None:
    for template in load_templates():
        check_public_base_url(template["base_url"])
        if template["base_url"]:
            assert urlsplit(template["base_url"]).scheme == "https"


def test_templates_have_no_key_like_fields() -> None:
    for template in load_templates():
        for field in template:
            assert not any(word in field.lower() for word in ("key", "token", "secret", "auth"))


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://user:pass@api.example.com/v1",
        "ftp://api.example.com/v1",
        "http://localhost:8000/v1",
        "http://10.0.0.5:8000/v1",
    ],
)
def test_non_public_base_url_is_rejected(bad_url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        check_public_base_url(bad_url)


def test_template_with_an_extra_field_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "templates.json"
    row = {
        "name": "x",
        "base_url": "https://api.example.com/v1",
        "api_type": "openai-chat",
        "hint": "",
        "verified": False,
        "api_key": "not-allowed",
    }
    path.write_text(json.dumps([row]), encoding="utf-8")
    with pytest.raises(ValueError, match="fields"):
        load_templates(path)
