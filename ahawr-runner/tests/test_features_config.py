from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from ahawr_runner import features_config
from ahawr_runner.features_config import (
    FEATURE_DEFAULTS,
    PARAM_MINIMUMS,
    _reject_constant,
    flatten,
    parse_features,
    validate_features_json,
)

CASES: dict[str, Any] = json.loads(
    (Path(__file__).parent / "features_cases.json").read_text(encoding="utf-8")
)
LEGACY: dict[str, Any] = CASES["legacy_constants"]

# Inputs of every form a features column can take, pinned per feature.  The partial
# object carries one parameter (the others missing) whose value is below its entry in
# PARAM_MINIMUMS: max_tasks 0 against the minimum 1, max_splits -1 against the minimum 0.
PARTIAL_INPUTS: dict[str, str] = {
    "plan_no_truncate": '{"plan_no_truncate": {"max_tasks": 0}}',
    "adaptive_split": '{"adaptive_split": {"max_splits": -1}}',
    "run_hygiene": '{"run_hygiene": {"enabled": true}}',
    "event_schema_version": '{"event_schema_version": {"enabled": true}}',
    "think_profiles": '{"think_profiles": {"enabled": true}}',
    "stats_popup": '{"stats_popup": {"enabled": true}}',
    "stats_engine_sampler": '{"stats_engine_sampler": {"enabled": true}}',
    "mission_dependencies": '{"mission_dependencies": {"enabled": true}}',
}
MISSION_OVERRIDE_INPUTS: dict[str, str] = {
    "plan_no_truncate": '{"plan_no_truncate": {"enabled": true, "max_tasks": 20}}',
    "adaptive_split": '{"adaptive_split": {"enabled": true, "split_steps": 100}}',
    "run_hygiene": '{"run_hygiene": {"enabled": true}}',
    "event_schema_version": '{"event_schema_version": {"enabled": true}}',
    "think_profiles": '{"think_profiles": {"enabled": true}}',
    "stats_popup": '{"stats_popup": {"enabled": true}}',
    "stats_engine_sampler": '{"stats_engine_sampler": {"enabled": true}}',
    "mission_dependencies": '{"mission_dependencies": {"enabled": true}}',
}
PINNED_FIXTURE = Path(__file__).parent / "fixtures" / "features_pinned.json"

# Every "<param>": True/False or "<param>": <number> pair in the parser source is a
# default living in code instead of features_defaults.json.
DEFAULT_SOURCE_LITERAL = re.compile(
    r'"(?:' + "|".join(("enabled", *sorted(PARAM_MINIMUMS))) + r')"\s*:\s*(?:True|False|-?\d+)'
)


def _literal_defaults(source_text: str) -> list[str]:
    """The defaults written in a given source of features_config.py."""
    return [match.group(0) for match in DEFAULT_SOURCE_LITERAL.finditer(source_text)]


def _shipped_defaults_text() -> str:
    """features_defaults.json as it ships next to the module."""
    return Path(features_config.DEFAULTS_PATH).read_text(encoding="utf-8")


def test_features_defaults_file_equals_the_two_tables() -> None:
    """FEATURE_DEFAULTS and PARAM_MINIMUMS are exactly the content of the JSON file."""
    data = json.loads(_shipped_defaults_text())
    assert data["features"] == FEATURE_DEFAULTS, "FEATURE_DEFAULTS differs from the JSON"
    assert data["param_minimums"] == PARAM_MINIMUMS, "PARAM_MINIMUMS differs from the JSON"


def test_features_defaults_source_text_has_no_literal_defaults() -> None:
    """The defaults come from the JSON file only: the parser source holds no literal
    default for a parameter, such as "split_steps": 150."""
    source_text = Path(features_config.__file__).read_text(encoding="utf-8")
    literals = _literal_defaults(source_text)
    assert literals == [], f"features_config.py still carries literal defaults: {literals}"


def test_features_defaults_are_all_off_by_default() -> None:
    """Each feature is off in the JSON, in the loaded table and in the resolved defaults."""
    for body in json.loads(_shipped_defaults_text())["features"].values():
        assert body["enabled"] is False
    for feature, body in FEATURE_DEFAULTS.items():
        assert body["enabled"] is False, f"{feature} is on by default"
    resolved, errors = parse_features("", "")
    assert errors == []
    assert resolved is not None
    for feature, body in resolved.items():
        assert body["enabled"] is False, f"{feature} resolves on with empty columns"


def test_features_defaults_missing_or_malformed_file_raises_with_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loader names the file and the reason; there is no silent literal fallback."""
    defaults = tmp_path / "features_defaults.json"
    monkeypatch.setattr(features_config, "DEFAULTS_PATH", defaults)

    defaults.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError, match=f"{defaults}: not valid JSON"):
        features_config._load_defaults()

    defaults.write_text('{"features": [], "param_minimums": 3}', encoding="utf-8")
    with pytest.raises(ValueError, match="features and param_minimums must be objects"):
        features_config._load_defaults()

    defaults.write_text(
        '{"features": {"adaptive_split": {"split_steps": 5}}, '
        '"param_minimums": {"split_steps": "one"}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match='minimum "split_steps" must be an integer'):
        features_config._load_defaults()

    defaults.write_text(
        '{"features": {"adaptive_split": []}, "param_minimums": {"split_steps": 1}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match='feature "adaptive_split" must be an object'):
        features_config._load_defaults()

    monkeypatch.setattr(features_config, "DEFAULTS_PATH", tmp_path / "absent_defaults.json")
    with pytest.raises(ValueError, match="cannot be read"):
        features_config._load_defaults()


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


def test_stats_popup_and_stats_engine_sampler_are_flags_default_off() -> None:
    """Both new flags are known, default off, read like the other flags, and add
    no legacy constant to flatten."""
    features, errors = parse_features("", "")
    assert errors == []
    assert features is not None
    assert features["stats_popup"] == {"enabled": False}
    assert features["stats_engine_sampler"] == {"enabled": False}
    assert flatten(features) == LEGACY
    flipped, errors = parse_features(
        '{"stats_popup": {"enabled": true}, "stats_engine_sampler": {"enabled": true}}',
        "",
    )
    assert errors == []
    assert flipped is not None
    assert flipped["stats_popup"]["enabled"] is True
    assert flipped["stats_engine_sampler"]["enabled"] is True
    assert flatten(flipped) == LEGACY
    mission_only, errors = parse_features("", '{"stats_popup": {"enabled": true}}')
    assert errors == []
    assert mission_only is not None
    assert mission_only["stats_popup"]["enabled"] is True
    bad, errors = parse_features('{"stats_popup": {"enabled": "yes"}}', "")
    assert bad is None
    assert errors == ['features_json: stats_popup.enabled must be true or false, got "yes"']
    bad, errors = parse_features('{"stats_engine_sampler": {"max_tasks": 3}}', "")
    assert bad is None
    assert errors == [
        'features_json: unknown parameter "max_tasks" in feature "stats_engine_sampler"'
    ]


def test_mission_dependencies_is_a_flag_default_off() -> None:
    """The flag is known, off when neither column is set, and adds no legacy constant
    to flatten: the dependency routes read it, no workflow node does."""
    features, errors = parse_features("", "")
    assert errors == []
    assert features is not None
    assert features["mission_dependencies"] == {"enabled": False}
    assert flatten(features) == LEGACY
    flipped, errors = parse_features('{"mission_dependencies": {"enabled": true}}', "")
    assert errors == []
    assert flipped is not None
    assert flipped["mission_dependencies"]["enabled"] is True
    assert flatten(flipped) == LEGACY
    mission_only, errors = parse_features("", '{"mission_dependencies": {"enabled": true}}')
    assert errors == []
    assert mission_only is not None
    assert mission_only["mission_dependencies"]["enabled"] is True
    bad, errors = parse_features('{"mission_dependencies": {"enabled": "yes"}}', "")
    assert bad is None
    assert errors == [
        'features_json: mission_dependencies.enabled must be true or false, got "yes"'
    ]
    bad, errors = parse_features('{"mission_dependencies": {"max_tasks": 3}}', "")
    assert bad is None
    assert errors == [
        'features_json: unknown parameter "max_tasks" in feature "mission_dependencies"'
    ]


def test_mission_dependencies_flag_is_declared_off_by_default() -> None:
    """The flag is declared in the single defaults file with enabled false, so empty
    columns resolve it off and it adds no legacy constant to flatten."""
    assert "mission_dependencies" in FEATURE_DEFAULTS, "the flag is not declared at all"
    assert FEATURE_DEFAULTS["mission_dependencies"] == {"enabled": False}
    shipped = json.loads(_shipped_defaults_text())
    assert shipped["features"]["mission_dependencies"] == {"enabled": False}
    features, errors = parse_features("", "")
    assert errors == []
    assert features is not None
    assert features["mission_dependencies"] == {"enabled": False}
    assert flatten(features) == LEGACY


def test_mission_dependencies_flag_turns_on_through_the_save_path() -> None:
    """The PUT routes store only what validate_features_json accepts; the exact text the
    dashboard sends is accepted in either column and resolves the flag on."""
    valid = '{"mission_dependencies": {"enabled": true}}'
    assert validate_features_json(valid, None) == ()
    assert validate_features_json("", valid) == ()
    resolved, errors = parse_features(valid, "")
    assert errors == []
    assert resolved is not None
    assert resolved["mission_dependencies"]["enabled"] is True
    assert flatten(resolved) == LEGACY
    mission_only, errors = parse_features("", valid)
    assert errors == []
    assert mission_only is not None
    assert mission_only["mission_dependencies"]["enabled"] is True


def test_mission_dependencies_flag_rejects_invalid_values_like_neighbouring_flags() -> None:
    """One bad payload naming the flag and a neighbouring flag returns the same error
    shape for both: the value must be a boolean, unknown parameters are refused."""
    bad = '{"mission_dependencies": {"enabled": "yes"}, "stats_popup": {"enabled": "yes"}}'
    assert validate_features_json(bad, None) == (
        'features_json: mission_dependencies.enabled must be true or false, got "yes"',
        'features_json: stats_popup.enabled must be true or false, got "yes"',
    )
    assert validate_features_json("", '{"mission_dependencies": {"enabled": null}}') == (
        'mission_features_json: mission_dependencies.enabled must be true or false, got null',
    )
    assert validate_features_json('{"mission_dependencies": {"max_tasks": 3}}', None) == (
        'features_json: unknown parameter "max_tasks" in feature "mission_dependencies"',
    )


PINNED_FORMS: tuple[str, ...] = (
    "empty_string",
    "empty_none",
    "empty_object",
    "absent_column",
    "partial_object",
    "mission_override",
)
PINNED_REGENERATE = "FEATURES_PINNED_REGENERATE"


def _pinned_inputs(feature: str) -> dict[str, list[str | None]]:
    """The two columns of parse_features for every pinned input form of one feature."""
    return {
        "empty_string": ["", ""],
        "empty_none": [None, None],
        "empty_object": ["{}", "{}"],
        "absent_column": ['{"run_hygiene": {"enabled": true}}', None],
        "partial_object": [PARTIAL_INPUTS[feature], ""],
        "mission_override": ["", MISSION_OVERRIDE_INPUTS[feature]],
    }


def build_pinned_fixture() -> dict[str, Any]:
    """The resolved values and flattened constants of every feature for every input
    form, as the current code computes them.  Run with the FEATURES_PINNED_REGENERATE
    environment variable set to rewrite tests/fixtures/features_pinned.json after a
    deliberate change of a default."""
    fixture: dict[str, Any] = {}
    for feature in sorted(FEATURE_DEFAULTS):
        entry: dict[str, Any] = {}
        for form, inputs in _pinned_inputs(feature).items():
            features, errors = parse_features(inputs[0], inputs[1])
            entry[form] = {
                "inputs": inputs,
                "resolved": features,
                "errors": errors,
                "flatten": flatten(features) if features is not None else None,
            }
        fixture[feature] = entry
    return fixture


def test_pinned_fixture_pins_current_resolution_and_flatten() -> None:
    """Characterization test: the fixture holds what parse_features and flatten produce
    today for every feature and every input form, so a later refactoring can prove it
    kept the behaviour.  Set FEATURES_PINNED_REGENERATE to rewrite the fixture after a
    deliberate change of a default; without the variable the fixture is compared only."""
    expected = build_pinned_fixture()
    if os.environ.get(PINNED_REGENERATE):
        text = json.dumps(expected, sort_keys=True, indent=2) + "\n"
        PINNED_FIXTURE.write_text(text, encoding="utf-8")
    fixture = json.loads(PINNED_FIXTURE.read_text(encoding="utf-8"))
    assert sorted(fixture) == sorted(FEATURE_DEFAULTS), "the fixture misses a feature"
    for feature, entry in fixture.items():
        assert sorted(entry) == sorted(PINNED_FORMS), f"{feature}: the input forms changed"
    assert fixture == expected, "the pinned fixture no longer matches the current code"
    for feature in FEATURE_DEFAULTS:
        resolved = fixture[feature]["empty_object"]["resolved"]
        assert resolved[feature]["enabled"] is False, f"{feature} is on by default"


def test_tables_are_read_from_the_packaged_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shipped features_defaults.json is the source of both tables; a missing or
    malformed file raises with the reason, never a silent literal fallback."""
    shipped = Path(features_config.__file__).with_name("features_defaults.json")
    data = json.loads(shipped.read_text(encoding="utf-8"))
    assert data["features"] == FEATURE_DEFAULTS, "the JSON and FEATURE_DEFAULTS differ"
    assert data["param_minimums"] == PARAM_MINIMUMS, "the JSON and PARAM_MINIMUMS differ"

    defaults = tmp_path / "features_defaults.json"
    monkeypatch.setattr(features_config, "DEFAULTS_PATH", defaults)
    defaults.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        features_config._load_defaults()

    bad_feature = '{"features": {"adaptive_split": []}, "param_minimums": {}}'
    defaults.write_text(bad_feature, encoding="utf-8")
    with pytest.raises(ValueError, match='feature "adaptive_split" must be an object'):
        features_config._load_defaults()

    partial = '{"features": {"adaptive_split": {"split_steps": 5}}, "param_minimums": {}}'
    defaults.write_text(partial, encoding="utf-8")
    with pytest.raises(ValueError, match="split_steps.*has no entry in param_minimums"):
        features_config._load_defaults()

    monkeypatch.setattr(features_config, "DEFAULTS_PATH", tmp_path / "absent_defaults.json")
    with pytest.raises(ValueError, match="cannot be read"):
        features_config._load_defaults()


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
