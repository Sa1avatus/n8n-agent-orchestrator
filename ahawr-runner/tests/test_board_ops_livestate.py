"""Live-state flag view/edit with optimistic locking (board_ops_livestate.py).

Covers the three acceptance criteria:
1. Only flags listed in the notes can be changed.
2. A stale version raises a conflict.
3. A changelog entry is written for each successful change.
"""

from __future__ import annotations

import pytest

from ahawr_runner.board_ops_livestate import (
    FLAG_TYPES,
    InMemoryLiveStateStore,
)

M = "m1"


def _store() -> InMemoryLiveStateStore:
    """Fresh in-memory store with a fixed clock."""
    ticks = iter([
        "2026-10-17T12:00:00+00:00",
        "2026-10-17T12:00:01+00:00",
        "2026-10-17T12:00:02+00:00",
        "2026-10-17T12:00:03+00:00",
        "2026-10-17T12:00:04+00:00",
        "2026-10-17T12:00:05+00:00",
        "2026-10-17T12:00:06+00:00",
    ])
    return InMemoryLiveStateStore(now_fn=lambda: next(ticks))


# ---------------------------------------------------------------------------
# Criterion 1: whitelist
# ---------------------------------------------------------------------------

def test_only_whitelisted_flags_can_be_changed():
    store = _store()
    # A whitelisted flag is accepted.
    result = store.set_flags(M, {"paused": True}, None)
    assert result["flags"]["paused"] is True
    # A non-whitelisted flag (retry_requested is NOT confirmed per the notes)
    # is rejected.
    with pytest.raises(ValueError):
        store.set_flags(M, {"retry_requested": True}, None)
    with pytest.raises(ValueError):
        store.set_flags(M, {"unknown_flag": "x"}, None)


def test_wrong_type_is_rejected():
    store = _store()
    # bool must be a real bool, not an int.
    with pytest.raises(ValueError):
        store.set_flags(M, {"paused": 1}, None)
    # str flag must be a string, not an int.
    with pytest.raises(ValueError):
        store.set_flags(M, {"pause_reason": 42}, None)
    # list flag must be a list, not a string.
    with pytest.raises(ValueError):
        store.set_flags(M, {"queue": "m1,m2"}, None)


def test_empty_changes_are_allowed():
    store = _store()
    store.set_flags(M, {"paused": True}, None)
    result = store.set_flags(M, {}, None)
    assert result["flags"]["paused"] is True


# ---------------------------------------------------------------------------
# Criterion 2: stale version raises conflict
# ---------------------------------------------------------------------------

def test_stale_version_raises_conflict():
    store = _store()
    first = store.set_flags(M, {"paused": True}, None)
    v1 = first["version"]
    # A stale version must conflict.
    with pytest.raises(Exception) as exc:
        store.set_flags(M, {"pause_reason": "x"}, "stale-version")
    assert "conflict" in str(exc).lower() or "expected version" in str(exc)
    # The correct version is accepted.
    result = store.set_flags(M, {"pause_reason": "x"}, v1)
    assert result["flags"]["pause_reason"] == "x"


def test_conflict_message_includes_versions():
    store = _store()
    first = store.set_flags(M, {"paused": True}, None)
    v1 = first["version"]
    with pytest.raises(Exception) as exc:
        store.set_flags(M, {"pause_reason": "x"}, "stale")
    msg = str(exc)
    assert "stale" in msg
    assert v1 in msg


def test_no_row_with_non_null_version_raises_conflict():
    store = _store()
    with pytest.raises(Exception) as exc:
        store.set_flags(M, {"paused": True}, "some-version")
    assert "got none" in str(exc)


# ---------------------------------------------------------------------------
# Criterion 3: changelog entry per successful change
# ---------------------------------------------------------------------------

def test_changelog_written_for_each_successful_change():
    store = _store()
    # First change creates a changelog entry.
    store.set_flags(M, {"paused": True}, None)
    log1 = store.changelog()
    assert len(log1) == 1
    assert log1[0].action == "live state flags set"
    assert log1[0].mission_id == M

    # Second change creates another entry.
    v1 = store.get_flags(M)["version"]
    store.set_flags(M, {"pause_reason": "r"}, v1)
    log2 = store.changelog()
    assert len(log2) == 2
    assert log2[0].action == "live state flags set"
    assert log2[0].mission_id == M
    # The newest entry is first.
    assert log2[0].after_json["pause_reason"] == "r"


def test_changelog_not_written_on_conflict():
    store = _store()
    first = store.set_flags(M, {"paused": True}, None)
    log_before = len(store.changelog())
    with pytest.raises(Exception):
        store.set_flags(M, {"pause_reason": "x"}, "stale")
    assert len(store.changelog()) == log_before


def test_get_flags_returns_flags_and_version():
    store = _store()
    result = store.set_flags(
        M, {"paused": True, "workflow_status": "stopped", "queue": ["m1", "m2"]}, None
    )
    assert result["flags"] == {
        "paused": True,
        "workflow_status": "stopped",
        "queue": ["m1", "m2"],
    }
    assert result["version"] is not None


def test_get_flags_missing_row():
    store = _store()
    result = store.get_flags("no-such-mission")
    assert result == {"flags": {}, "version": None}


# board_ops_store_pg.py has no test file of its own: the helpers it shares with
# board_pg.py (the _iso duplicate body) are pinned here, through the module that
# imports them.
def test_ops_store_iso_formats_a_timestamp_column_and_stringises_the_rest() -> None:
    """_iso of the Postgres ops store: None stays None, a value with isoformat() is
    formatted, everything else becomes a str (a text timestamp is returned as read)."""
    from datetime import UTC, datetime

    from ahawr_runner.board_common import _iso

    stamp = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert _iso(None) is None
    assert _iso(stamp) == "2026-01-01T12:00:00+00:00"
    assert _iso("2026-01-01T12:00:00+00:00") == "2026-01-01T12:00:00+00:00"
    assert _iso(1767225600) == "1767225600"
    assert _iso("") == ""


def test_ops_store_read_json_keeps_a_parsed_column_and_the_raw_text_otherwise() -> None:
    """_read_json of the Postgres ops store: None stays None, a dict/list from the
    driver is passed through, text is parsed, unparseable text is returned as read."""
    from ahawr_runner.board_ops_store_pg import _read_json

    assert _read_json(None) is None
    assert _read_json({"tasks": []}) == {"tasks": []}
    assert _read_json([1, 2]) == [1, 2]
    assert _read_json('{"tasks": []}') == {"tasks": []}
    assert _read_json("not json") == "not json"
    assert _read_json("") == ""
    assert _read_json(5) == 5
