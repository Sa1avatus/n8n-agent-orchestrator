"""Conformance: every line of the recorded event fixtures passes the event validator.

Provenance of the fixtures:
- `fixtures/events_claude_code.jsonl`: a 38-line excerpt of the live log
  `/data/events/run_b9cc0264d28d41348c9e7714818ffa9b.jsonl`, with the work directory
  replaced by `/workspace`, the model name unchanged and long prompt/thinking/tool
  text clipped. No secrets, no personal absolute paths.
- `fixtures/events_hermes.jsonl`: written by `EventLog.add` (`events.py:349`) over the
  entries `hermes_events.result_entries` (`hermes_events.py:104-136`) produces for the
  payload shapes of `docs/hermes-engine.md:128-130`. No recorded Hermes event log exists
  on the host (`docs/run-event-format.md:301-306`), so this fixture is constructed from
  the writer, not copied from a log.

No Codex event or file is used: the Codex fixtures live in `fixtures/codex/` and are read
only by `tests/test_codex_events.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ahawr_runner.contract_schema import EVENT_KIND_FIELDS, validate_run_event

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CLAUDE_FIXTURE = FIXTURES / "events_claude_code.jsonl"
HERMES_FIXTURE = FIXTURES / "events_hermes.jsonl"
# the task asks for a short excerpt per engine, in lines
MAX_FIXTURE_LINES = 50


def fixture_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_claude_code_fixture_events_pass_the_validator() -> None:
    events = fixture_lines(CLAUDE_FIXTURE)
    assert events, f"{CLAUDE_FIXTURE.name} is empty"
    assert len(events) <= MAX_FIXTURE_LINES
    for seq, event in enumerate(events, 1):
        errors = validate_run_event(event)
        assert not errors, f"line {seq} (kind {event.get('kind')}): {'; '.join(errors)}"


def test_hermes_fixture_events_pass_the_validator() -> None:
    events = fixture_lines(HERMES_FIXTURE)
    assert events, f"{HERMES_FIXTURE.name} is empty"
    assert len(events) <= MAX_FIXTURE_LINES
    for seq, event in enumerate(events, 1):
        errors = validate_run_event(event)
        assert not errors, f"line {seq} (kind {event.get('kind')}): {'; '.join(errors)}"


def test_every_kind_seen_in_the_fixtures_is_known_to_the_schema() -> None:
    kinds = {
        event["kind"]
        for path in (CLAUDE_FIXTURE, HERMES_FIXTURE)
        for event in fixture_lines(path)
    }
    unknown = sorted(k for k in kinds if k not in EVENT_KIND_FIELDS)
    assert not unknown, f"event kinds not in the table: {unknown}"
    assert kinds, "the fixtures carry no event kind"


def test_the_fixtures_hold_no_codex_event() -> None:
    for path in (CLAUDE_FIXTURE, HERMES_FIXTURE):
        assert "codex" not in path.read_text(encoding="utf-8").lower(), path.name


def test_the_envelope_of_every_fixture_line_is_stamped() -> None:
    for path in (CLAUDE_FIXTURE, HERMES_FIXTURE):
        for event in fixture_lines(path):
            assert isinstance(event.get("seq"), int) and event["seq"] > 0, event
            assert isinstance(event.get("t"), float), event
