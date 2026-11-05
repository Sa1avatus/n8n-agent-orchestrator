"""EventLog (ahawr_runner/events.py): the ``schema_version`` stamp behind the
``event_schema_version`` feature, and the ``_read_file`` validation hook."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from ahawr_runner.events import EventLog

VALID_LINE = '{"seq": 1, "t": 2.0, "kind": "text", "text": "all good"}\n'
BAD_LINE = '{"t": 3.0, "kind": "text", "text": "no seq here"}\n'
ENTRY = {"kind": "text", "text": "hello"}
FIXED_TIME = 1_700_000_000.0
# the exact bytes the writer emits for ENTRY with the feature off, today
OFF_BYTES = b'{"seq": 1, "t": 1700000000.0, "kind": "text", "text": "hello"}\n'


def _log(tmp_path: Path, name: str, stamp: bool) -> EventLog:
    return EventLog(tmp_path / name, retention_days=0, event_schema_version=stamp)


def test_a_valid_line_is_returned_as_is(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "run-1.jsonl"
    path.write_text(VALID_LINE, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        entries = EventLog._read_file(path)
    assert entries == [{"seq": 1, "t": 2.0, "kind": "text", "text": "all good"}]
    assert "invalid run event" not in caplog.text


def test_bad_line_warns_and_returns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "run-2.jsonl"
    path.write_text(BAD_LINE, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        entries = EventLog._read_file(path)
    assert entries == [{"t": 3.0, "kind": "text", "text": "no seq here"}]
    assert "missing required field seq in run event" in caplog.text
    assert "no seq here" not in caplog.text


def test_flag_off_writes_the_bytes_of_before(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """The off path writes byte-for-byte the same line as before the feature existed."""
    monkeypatch.setattr(time, "time", lambda: FIXED_TIME)
    log = _log(tmp_path, "off", False)
    log.open("run-a")
    log.add("run-a", ENTRY)
    log.close("run-a")
    written = (tmp_path / "off" / "run-a.jsonl").read_bytes()
    assert written == OFF_BYTES


def test_flag_on_stamps_every_new_event(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Every event the writer appends carries schema_version 1, before seq and t."""
    monkeypatch.setattr(time, "time", lambda: FIXED_TIME)
    log = _log(tmp_path, "on", True)
    log.open("run-b")
    log.add("run-b", ENTRY)
    log.add("run-b", {"kind": "end", "status": "done", "error_code": "", "error": ""})
    entries, _ = log.read("run-b")
    assert all(entry["schema_version"] == 1 for entry in entries)
    log.close("run-b")
    first, second = (tmp_path / "on" / "run-b.jsonl").read_text(encoding="utf-8").splitlines()
    expected = json.dumps(
        {"schema_version": 1, "seq": 1, "t": FIXED_TIME, "kind": "text", "text": "hello"},
        ensure_ascii=False,
    )
    assert first == expected
    assert second.startswith('{"schema_version": 1, "seq": 2, "t": ')


def test_flag_on_bytes_are_the_off_bytes_plus_the_stamp(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The only difference is the stamp: key order and separators stay the same."""
    monkeypatch.setattr(time, "time", lambda: FIXED_TIME)
    off, on = _log(tmp_path, "off", False), _log(tmp_path, "on", True)
    off.open("run-c")
    on.open("run-c")
    off.add("run-c", ENTRY)
    on.add("run-c", ENTRY)
    off.close("run-c")
    on.close("run-c")
    off_line = (tmp_path / "off" / "run-c.jsonl").read_text(encoding="utf-8")
    on_line = (tmp_path / "on" / "run-c.jsonl").read_text(encoding="utf-8")
    assert on_line.replace('{"schema_version": 1, ', "{") == off_line


def test_the_flag_defaults_off(tmp_path: Path) -> None:
    assert EventLog(tmp_path / "default").event_schema_version is False


def test_read_accepts_events_without_the_stamp(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing stamp is version 0: the validator hook accepts it, the reader keeps it."""
    path = tmp_path / "old.jsonl"
    path.write_text(VALID_LINE, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        entries = EventLog._read_file(path)
    assert entries == [{"seq": 1, "t": 2.0, "kind": "text", "text": "all good"}]
    assert entries[0].get("schema_version", 0) == 0
    assert "invalid run event" not in caplog.text
