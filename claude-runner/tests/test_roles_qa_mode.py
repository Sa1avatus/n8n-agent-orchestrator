from __future__ import annotations

import json

import pytest

from claude_runner.board_ready import check_qa_ready
from claude_runner.roles_config import QA_MODES, resolve


def test_missing_qa_entry_is_off():
    assert resolve({}, "qa", 1, {}).options["mode"] == "off"
    item = check_qa_ready({}, {})
    assert item["status"] == "warning"
    assert item["mode"] == "off"


@pytest.mark.parametrize(
    "qa",
    ["anthropic/sonnet", {"1": "anthropic/sonnet"}, {"1": "anthropic/sonnet", "options": {}}],
)
def test_missing_options_or_mode_is_off(qa):
    assert resolve({"qa": qa}, "qa", 1, {}).options["mode"] == "off"
    assert check_qa_ready({"qa": qa}, {})["mode"] == "off"


@pytest.mark.parametrize("mode", QA_MODES)
def test_mode_round_trips_through_roles_json(mode):
    roles = {"qa": {"1": "anthropic/sonnet", "options": {"mode": mode}}}
    restored = json.loads(json.dumps(roles))
    assert resolve(restored, "qa", 1, {}).options["mode"] == mode


def test_unknown_mode_is_rejected_not_mapped_to_off():
    roles = {"qa": {"1": "anthropic/sonnet", "options": {"mode": "weekly"}}}
    assert resolve(roles, "qa", 1, {}).options["mode"] == "weekly"
    item = check_qa_ready(roles, {})
    assert item["status"] == "error"
    assert "'weekly'" in item["reason"]
