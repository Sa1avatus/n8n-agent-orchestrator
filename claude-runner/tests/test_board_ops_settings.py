"""Settings registry: defaults, validation, diff, and apply against a
``board.changelog`` row per changed key (InMemoryOpsStore)."""

from __future__ import annotations

import pytest

from claude_runner.board import ConflictError
from claude_runner.board_ops_settings import apply, defaults, diff, validate
from claude_runner.board_ops_store import InMemoryOpsStore


def test_defaults_for_loop_protection_keys():
    d = defaults()
    assert (
        d["no_progress_retries"],
        d["max_mission_runs"],
        d["consecutive_failures_stop"],
        d["docker_wait_minutes"],
        d["max_failed_launches"],
    ) == (3, 15, 2, 120, 6)
    assert d["ops_rate_limit_per_min"] == 6
    assert d["command_timeout_s"] == 600
    assert d["only_failure_or_done"] is True


def test_validate_rejects_unknown_out_of_range_and_wrong_type():
    assert validate("no_progress_retries", 3) == []
    assert validate("no_progress_retries", 0)
    assert validate("no_progress_retries", 11)
    assert validate("no_progress_retries", True)
    assert validate("no_progress_retries", "3")
    assert validate("unknown_key", 1)
    # readable messages name the key
    assert "no_progress_retries" in validate("no_progress_retries", 11)[0]
    assert "unknown setting key" in validate("unknown_key", 1)[0]


def test_diff_lists_only_changed_keys():
    old = {"no_progress_retries": 3, "max_mission_runs": 15}
    new = {"no_progress_retries": 4, "max_mission_runs": 15}
    d = diff(old, new)
    assert d == {"no_progress_retries": {"old": 3, "new": 4}}


def test_apply_writes_one_changelog_row_per_changed_key():
    store = InMemoryOpsStore()
    changes = {
        "no_progress_retries": 4,
        "max_mission_runs": 20,
        "only_failure_or_done": False,
    }
    written = apply(store, store.changelog(), "op", changes)
    assert len(written) == 3
    # distinct ids
    assert len({e.id for e in written}) == 3
    # each row has a before (new key -> None) and after
    for key, e in zip(changes, written):
        assert e.mission_id == key
        assert e.who == "op"
        if store.get_setting(key) is None:
            raise AssertionError("key not written")
        assert e.after_json["value_json"] == changes[key]
    # rows are really stored
    rows = store.changelog()
    stored_ids = {e.id for e in rows if e.mission_id in changes}
    assert stored_ids == {e.id for e in written}


def test_apply_skips_unchanged_keys():
    store = InMemoryOpsStore()
    # seed the two keys that will change, and the one that will not
    store.put_setting("no_progress_retries", 3, None)
    store.put_setting("max_mission_runs", 10, None)
    store.put_setting("only_failure_or_done", True, None)
    # changing 2 of 3 keys; the third is unchanged
    changes = {
        "no_progress_retries": 4,   # changed
        "max_mission_runs": 15,     # changed
        "only_failure_or_done": True,  # unchanged (stored True == new True)
    }
    written = apply(store, store.changelog(), "op", changes)
    # exactly 2 stored rows (the unchanged key gives 0)
    assert len(written) == 2
    assert {e.mission_id for e in written} == {"no_progress_retries", "max_mission_runs"}


def test_apply_stale_version_on_second_key_leaves_first_key_unwritten():
    # a counting clock: the wall clock of Windows can stamp three quick writes with the same instant, and then
    # the "stale" version equals the stored one and no conflict can be seen
    ticks = iter(range(1, 1000))
    store = InMemoryOpsStore(now_fn=lambda: "2026-10-18T00:00:%06d+00:00" % next(ticks))
    # seed both keys
    s1 = store.put_setting("no_progress_retries", 3, None)
    store.put_setting("max_mission_runs", 15, None)
    # versions: no_progress_retries fresh, max_mission_runs stale (we bumped it)
    store.put_setting("max_mission_runs", 20, store.get_setting("max_mission_runs").updated_at)
    versions = {
        "no_progress_retries": s1.updated_at,  # fresh
        "max_mission_runs": s1.updated_at,     # stale (wrong row's stamp)
    }
    changes = {"no_progress_retries": 4, "max_mission_runs": 25}
    with pytest.raises(ConflictError):
        apply(store, store.changelog(), "op", changes, versions=versions)
    # nothing was written: no_progress_retries still 3
    assert store.get_setting("no_progress_retries").value_json == 3
    # and no setting rows were added at all
    assert store.get_setting("no_progress_retries").value_json == 3


def test_apply_out_of_range_value_raises():
    store = InMemoryOpsStore()
    with pytest.raises(ValueError):
        apply(store, store.changelog(), "op", {"no_progress_retries": 11})
    # nothing written
    assert store.get_setting("no_progress_retries") is None
