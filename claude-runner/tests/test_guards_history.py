from __future__ import annotations

import json
from pathlib import Path

from claude_runner.guards_history import (
    HISTORY_MAX_ENTRIES,
    THIN_MAX_POINTS,
    GuardHistory,
)

NOW = 1_000_000.0


def test_ring_is_bounded() -> None:
    history = GuardHistory(max_entries=5)
    for i in range(12):
        history.record("g", "ok", None, 1, now=NOW + i)
    assert history.summary()["g"]["checks"] == 5
    assert HISTORY_MAX_ENTRIES == 500


def test_persistence_round_trip(tmp_path: Path) -> None:
    path = str(tmp_path / "h.json")
    first = GuardHistory(path)
    first.record("g", "error", "did a thing", 7, now=NOW)
    first.save(NOW)
    second = GuardHistory(path)
    entries = second.window("1h", now=NOW + 10)["g"]
    assert entries[0]["result"] == "error" and entries[0]["action"] == "did a thing"


def test_broken_file_means_empty_history(tmp_path: Path) -> None:
    path = tmp_path / "h.json"
    path.write_text("{not json", encoding="utf-8")
    assert GuardHistory(str(path)).summary() == {}
    path.write_text(json.dumps({"g": [{"t": "x"}]}), encoding="utf-8")
    assert GuardHistory(str(path)).summary() == {}


def test_thinning_keeps_errors_and_actions() -> None:
    history = GuardHistory(max_entries=1000)
    for i in range(900):
        history.record("g", "ok", None, 1, now=NOW + i)
    history.record("g", "error", None, 1, now=NOW + 901)
    history.record("g", "ok", "acted", 1, now=NOW + 902)
    out = history.window("24h", now=NOW + 903)["g"]
    assert len(out) <= THIN_MAX_POINTS
    assert any(e["result"] == "error" for e in out)
    assert any(e["action"] == "acted" for e in out)


def test_window_drops_old_entries() -> None:
    history = GuardHistory()
    history.record("g", "ok", None, 1, now=NOW)
    history.record("g", "ok", None, 1, now=NOW + 7200)
    assert len(history.window("1h", now=NOW + 7201)["g"]) == 1
    assert len(history.window("24h", now=NOW + 7201)["g"]) == 2


def test_long_action_is_cut() -> None:
    history = GuardHistory()
    history.record("g", "ok", "x" * 1000, 1, now=NOW)
    assert len(str(history.window("1h", now=NOW)["g"][0]["action"])) == 200
