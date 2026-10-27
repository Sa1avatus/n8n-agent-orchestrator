from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from claude_runner.config import _providers
from claude_runner.provider_registry import ProviderRegistry
from claude_runner.provider_secrets import ProviderSecrets
from claude_runner.roles_config import (
    RolesReport,
    legacy_role,
    merge,
    qa_layers,
    resolve,
    split_model,
    validate_roles,
    validate_roles_json,
)

CASES: dict[str, Any] = json.loads(
    (Path(__file__).parent / "roles_cases.json").read_text(encoding="utf-8")
)
PROVIDERS: list[str] = CASES["providers"]

MESSAGE_FRAGMENTS = {
    "first_key": "first attempt key",
    "key_order": "is not greater than",
    "bad_key": "must be a positive integer",
    "duplicate_key": "duplicate key",
    "bad_role_value": "value must be a model string or an object",
    "array_model": "model must be a string",
    "object_model": "model must be a string",
    "unknown_provider": "unknown provider",
    "empty_model": "has no model name",
    "empty_value": "has no model name",
    "too_many_keys": "keys, at most",
    "too_many_models": "distinct models",
    "unknown_option": "unknown option",
    "unknown_role": "is not a known role",
    "bad_escalate": "must be a whole number",
    "unknown_layer": "unknown layer",
    "repeated_layer": "repeats layer",
    "layers_not_list": "must be a list of layer names",
}


def _matches(expected: dict[str, str], message: str) -> bool:
    element = expected["element"].split(".")[-1]
    return MESSAGE_FRAGMENTS[expected["code"]] in message and element in message


def _assert_expected(expected: list[dict[str, str]], actual: tuple[str, ...]) -> None:
    if not expected:
        assert actual == ()
    for item in expected:
        assert any(_matches(item, message) for message in actual), (item, actual)


@pytest.mark.parametrize("case", CASES["validation"], ids=lambda case: case["name"])
def test_shared_validation_case(case: dict[str, Any]) -> None:
    report = validate_roles_json(case["input"], PROVIDERS)
    _assert_expected(case["errors"], report.errors)
    _assert_expected(case["warnings"], report.warnings)


@pytest.mark.parametrize("case", CASES["resolve"], ids=lambda case: case["name"])
def test_shared_resolve_case(case: dict[str, Any]) -> None:
    roles = merge(case["global"], case["mission"])
    resolved = resolve(roles, case["role"], case["attempt"], case["legacy"], case.get("facts"))
    expect = case["expect"]
    assert resolved.provider == expect["provider"]
    assert resolved.model == expect["model"]
    if "think" in expect:
        assert resolved.think == expect["think"]
    if "fresh" in expect:
        assert resolved.fresh == expect["fresh"]
    for key, value in expect.get("options", {}).items():
        assert resolved.options.get(key) == value


def test_split_model_keeps_nested_path_in_model_name() -> None:
    assert split_model("openrouter/deepseek/deepseek-chat") == (
        "openrouter",
        "deepseek/deepseek-chat",
    )


def test_merge_mission_replaces_role_and_keeps_other_roles() -> None:
    global_roles = {"worker": "anthropic/haiku", "qa": "anthropic/sonnet"}
    merged = merge(global_roles, {"worker": "local/m"})
    assert merged == {"worker": "local/m", "qa": "anthropic/sonnet"}
    assert global_roles == {"worker": "anthropic/haiku", "qa": "anthropic/sonnet"}


def test_merge_without_either_side_is_empty() -> None:
    assert merge(None, None) == {}


def test_legacy_role_without_provider_column_has_no_first_attempt() -> None:
    assert legacy_role({"worker_model": "haiku"}, "worker") == {"options": {}}


def test_resolve_legacy_model_without_provider_column_raises() -> None:
    with pytest.raises(ValueError, match="no model configured for role 'worker'"):
        resolve({}, "worker", 1, {"worker_model": "haiku"})


def test_resolve_global_role_ignores_absent_legacy_columns() -> None:
    resolved = resolve({"worker": "anthropic/haiku"}, "worker", 1, {})
    assert (resolved.provider, resolved.model) == ("anthropic", "haiku")


def test_qa_without_roles_or_legacy_resolves_to_defaults() -> None:
    resolved = resolve(merge(None, None), "qa", 1, {})
    assert (resolved.provider, resolved.model) == ("anthropic", "sonnet")
    assert resolved.options["mode"] == "off"
    assert resolved.options["max_minutes"] == 20
    assert resolved.options["max_cost_usd"] == 1


def test_qa_options_from_roles_json_override_defaults() -> None:
    roles = {"qa": {"1": "anthropic/haiku", "options": {"mode": "final", "max_minutes": 5}}}
    resolved = resolve(roles, "qa", 1, {})
    assert resolved.options["mode"] == "final"
    assert resolved.options["max_minutes"] == 5
    assert resolved.options["max_cost_usd"] == 1


def test_qa_validation_accepts_modes_and_limits() -> None:
    options = {"mode": "nightly", "max_minutes": 20, "max_cost_usd": 1.5}
    qa = {"1": "anthropic/sonnet", "options": options}
    assert validate_roles({"qa": qa}, PROVIDERS).errors == ()


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "always"},
        {"mode": None},
        {"max_minutes": -1},
        {"max_minutes": 0},
        {"max_minutes": "20"},
        {"max_minutes": True},
        {"max_cost_usd": -0.5},
        {"max_cost_usd": float("inf")},
    ],
)
def test_qa_validation_rejects_bad_mode_or_limit_with_reason(options: dict[str, Any]) -> None:
    report = validate_roles({"qa": {"1": "anthropic/sonnet", "options": options}}, PROVIDERS)
    assert len(report.errors) == 1
    assert "role 'qa'" in report.errors[0]
    assert "option" in report.errors[0]


def test_qa_without_layers_resolves_to_exact_options() -> None:
    roles = {"qa": {"1": "anthropic/haiku", "options": {"mode": "final"}}}
    assert validate_roles(roles, PROVIDERS).errors == ()
    assert resolve(roles, "qa", 1, {}).options == {
        "fresh": "auto",
        "mode": "final",
        "max_minutes": 20,
        "max_cost_usd": 1,
    }


def test_qa_layers_missing_or_empty_means_ui() -> None:
    assert qa_layers({}) == ["ui"]
    assert qa_layers({"layers": []}) == ["ui"]
    assert qa_layers({"layers": ["regression", "api"]}) == ["regression", "api"]


def test_qa_smoke_missing_is_false_and_absent_from_resolved_options() -> None:
    roles = {"qa": {"1": "anthropic/haiku", "options": {"mode": "final"}}}
    assert validate_roles(roles, PROVIDERS).errors == ()
    assert "smoke" not in resolve(roles, "qa", 1, {}).options
    assert resolve(roles, "qa", 1, {}).options.get("smoke") is not True


def test_qa_smoke_without_options_object_is_false() -> None:
    roles = {"qa": {"1": "anthropic/haiku"}}
    assert validate_roles(roles, PROVIDERS).errors == ()
    assert resolve(roles, "qa", 1, {}).options.get("smoke") is not True


def test_qa_smoke_true_is_read_as_true() -> None:
    roles = {"qa": {"1": "anthropic/haiku", "options": {"mode": "final", "smoke": True}}}
    assert validate_roles(roles, PROVIDERS).errors == ()
    assert resolve(roles, "qa", 1, {}).options["smoke"] is True


@pytest.mark.parametrize("smoke", ["yes", "true", 1, 0, None])
def test_qa_smoke_non_bool_is_rejected_with_reason(smoke: Any) -> None:
    roles = {"qa": {"1": "anthropic/haiku", "options": {"smoke": smoke}}}
    report = validate_roles(roles, PROVIDERS)
    assert report.errors == ("role 'qa': option 'smoke' must be true or false",)


def test_qa_only_options_are_unknown_for_other_roles() -> None:
    roles = {"worker": {"1": "anthropic/haiku", "options": {"mode": "off"}}}
    report = validate_roles(roles, PROVIDERS)
    assert report.errors == ("role 'worker': unknown option 'mode'",)


REGISTRY_ENV = {
    "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_BASE_URL": "http://litellm:4000",
    "CLAUDE_RUNNER_PROVIDER_OTHER__ANTHROPIC_BASE_URL": "http://other:4000",
}

GOLDEN_FLAG_OFF: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ('{"worker": "LOCAL/qwen"}', (), ()),
    (
        '{"worker": "local/qwen"}',
        ("role 'worker', model: unknown provider 'local'",),
        (),
    ),
    ('{"qa": {"1": "LOCAL/sonnet", "options": {"mode": "final", "smoke": true}}}', (), ()),
    (
        '{"qa": {"1": "LOCAL/sonnet", "options": {"mode": "always"}}}',
        ("role 'qa': option 'mode' 'always' must be one of off, final, ui_tasks, nightly",),
        (),
    ),
    (
        '{"qa": {"1": "LOCAL/x", "options": {"layers": ["api", "api"]}}}',
        ("role 'qa': option 'layers' repeats layer 'api'",),
        (),
    ),
    ('{"qa": {"1": "NOPE/x"}}', ("role 'qa', attempt '1': unknown provider 'NOPE'",), ()),
    ('{"worker": {"1": "LOCAL/a", "2": "OTHER/b"}, "reviewer": "LOCAL/c"}', (), ()),
    (
        '{"architect": "LOCAL/a", "bogus": "LOCAL/b"}',
        (),
        ("role 'bogus' is not a known role",),
    ),
    (
        '{"worker": "LOCAL/a", "worker_escalate": {"model": "OTHER/b", "above_steps": 0}}',
        ("role 'worker_escalate': 'above_steps' must be a whole number of at least 1",),
        (),
    ),
    (
        '{"worker": "LOCAL/a", "worker_escalate": '
        '{"model": "OTHER/b", "above_steps": 1, "from_attempt": 1}}',
        ("role 'worker_escalate': 'from_attempt' must be a whole number of at least 2",),
        (),
    ),
    ("[]", ("roles_json must be a JSON object",), ()),
    (
        '{"worker": "LOCAL/a", "worker": "LOCAL/b"}',
        ("roles_json has a duplicate key 'worker'",),
        (),
    ),
]


class _ProviderRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def providers_updated_at(self) -> str:
        return "2026-10-26T10:00:00+00:00"

    def list_providers(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]


def _db_row(name: str, enabled: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "enabled": enabled,
        "base_url": f"http://{name}:4000",
        "tools": "",
        "compact_pct": None,
        "compact_min_pct": None,
        "max_output_tokens": None,
        "search_first": None,
        "retry_fresh_session": None,
    }


@pytest.mark.parametrize(("text", "errors", "warnings"), GOLDEN_FLAG_OFF)
def test_flag_off_validation_matches_golden(
    tmp_path: Path, text: str, errors: tuple[str, ...], warnings: tuple[str, ...]
) -> None:
    registry = ProviderRegistry(
        _providers(REGISTRY_ENV),
        providers_db=False,
        store=_ProviderRows([]),
        secrets=ProviderSecrets(tmp_path / "secrets.json"),
    )
    report = validate_roles_json(text, list(registry.profiles()))
    assert report == RolesReport(errors=errors, warnings=warnings)


def test_flag_on_accepts_database_provider_and_rejects_disabled_one(tmp_path: Path) -> None:
    store = _ProviderRows([_db_row("acme"), _db_row("beta", enabled=False)])
    registry = ProviderRegistry(
        _providers(REGISTRY_ENV),
        providers_db=True,
        store=store,
        secrets=ProviderSecrets(tmp_path / "secrets.json"),
    )
    names = list(registry.profiles())
    roles = '{"worker": "ACME/gpt", "qa": {"1": "ACME/sonnet", "options": {"mode": "final"}}}'
    assert validate_roles_json(roles, names).errors == ()
    assert validate_roles_json('{"worker": "BETA/gpt"}', names).errors == (
        "role 'worker', model: unknown provider 'BETA'",
    )
