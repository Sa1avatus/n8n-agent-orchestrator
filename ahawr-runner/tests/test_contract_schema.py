"""Validators for provider blocks and run events: valid input, missing required
fields, wrong types, unknown keys, unknown kinds and schema_version 0/1."""

from __future__ import annotations

from ahawr_runner.contract_schema import validate_provider_block, validate_run_event

VALID_BLOCK = {
    "ENGINE": "hermes",
    "TOOLS": "Bash,Read",
    "COMPACT_MIN_TOKENS": 40000,
    "COMPACT_MIN_PCT": 3.0,
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": 8000,
    "HERMES_MAX_POLLS": 20,
    "SEARCH_FIRST": True,
    "schema_version": 1,
}


VALID_EVENT = {
    "seq": 3,
    "t": 10.5,
    "kind": "text",
    "text": "hello there",
    "agent": "worker",
    "step": 1,
    "schema_version": 1,
}


def test_provider_block_valid() -> None:
    assert validate_provider_block(VALID_BLOCK) == []


def test_provider_block_no_schema_version_is_accepted_as_zero() -> None:
    block = {"ENGINE": "claude", "COMPACT_MIN_TOKENS": 1200}
    assert validate_provider_block(block) == []


def test_provider_block_wrong_type_names_the_field() -> None:
    errors = validate_provider_block({"COMPACT_MIN_TOKENS": "not-a-number"})
    assert len(errors) == 1
    assert "field compact_min_tokens (key COMPACT_MIN_TOKENS)" in errors[0]
    assert "expected int, got str" in errors[0]


def test_provider_block_wrong_engine_value() -> None:
    errors = validate_provider_block({"ENGINE": "zap"})
    assert len(errors) == 1
    assert "field engine (key ENGINE)" in errors[0]
    assert "expected engine, got str" in errors[0]


def test_provider_block_unknown_key_is_ignored() -> None:
    block = {"MYSTERY_SETTING": "whatever", "ENGINE": "claude"}
    assert validate_provider_block(block) == []


def test_provider_block_has_no_required_fields() -> None:
    # every key of the table is optional: an empty block is valid
    assert validate_provider_block({}) == []


def test_provider_block_bad_schema_version() -> None:
    errors = validate_provider_block({"schema_version": "one"})
    assert len(errors) == 1
    assert "schema_version" in errors[0]
    assert "expected int, got str" in errors[0]


def test_run_event_valid() -> None:
    assert validate_run_event(VALID_EVENT) == []


def test_run_event_no_schema_version_is_accepted_as_zero() -> None:
    event = {"seq": 0, "t": 1.0, "kind": "notice", "text": "heads up"}
    assert validate_run_event(event) == []


def test_run_event_missing_envelope_field_names_the_field() -> None:
    event = {"t": 10.5, "kind": "text", "text": "hello"}
    errors = validate_run_event(event)
    assert len(errors) == 1
    assert "missing required field seq in run event" in errors[0]


def test_run_event_missing_kind_field_names_the_field() -> None:
    event = {"seq": 1, "t": 2.0, "kind": "tool_use", "id": "call-7", "name": "Bash"}
    errors = validate_run_event(event)
    assert len(errors) == 1
    assert "missing required field input in kind tool_use" in errors[0]


def test_run_event_wrong_type() -> None:
    event = dict(VALID_EVENT, seq="three")
    errors = validate_run_event(event)
    assert len(errors) == 1
    assert "field seq in run event: expected int, got str" in errors[0]


def test_run_event_wrong_type_of_a_kind_field() -> None:
    event = {"seq": 1, "t": 1.0, "kind": "tool_result", "id": "call-7", "text": "ok"}
    event["is_error"] = "no"
    errors = validate_run_event(event)
    assert len(errors) == 1
    assert "field is_error in kind tool_result: expected bool, got str" in errors[0]


def test_run_event_unknown_field_is_ignored() -> None:
    event = dict(VALID_EVENT, extra_marker={"nested": 42}, made_up_name=7)
    assert validate_run_event(event) == []


def test_run_event_unknown_kind() -> None:
    event = {"seq": 2, "t": 1.0, "kind": "banana"}
    errors = validate_run_event(event)
    assert len(errors) == 1
    assert "unknown event kind 'banana'" in errors[0]


def test_run_event_bad_schema_version() -> None:
    event = dict(VALID_EVENT, schema_version="v1")
    errors = validate_run_event(event)
    assert len(errors) == 1
    assert "schema_version" in errors[0]
    assert "expected int, got str" in errors[0]
