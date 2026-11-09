"""Incremental engine log collector (ahawr_runner/engine_collector.py).

The collector reads a real temporary log file from its stored byte offset:
append between polls, truncation (size below the offset restarts from 0), the
restart marker 'engine started:' incrementing engine_run, and a partial last
line kept until its newline arrives. The batch timestamp comes from an
injectable UTC clock. The loop's poll delay, the database retry backoff and
the exit after the retries are spent are tested with a fake sleep, and the
stop event ends the loop. No real database: the fake connection records the
parameters of every insert and can raise to force retries.

The dashboard wiring (``board_api.start_engine_collector``) is tested here too:
the collector thread starts only with the ``stats_engine_sampler`` flag on, does
not start with an unavailable store or one without a connection, and its thread
ends when the stop event is set or the bounded database retries are spent.
"""

import json
import threading
import time
import types
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ahawr_runner import board_api
from ahawr_runner.board import InMemoryBoardStore
from ahawr_runner.board_pg import UnavailableBoardStore
from ahawr_runner.engine_collector import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_ENGINE_LOG_PATH,
    DEFAULT_MAX_BATCH_LINES,
    DEFAULT_MAX_DB_RETRIES,
    DEFAULT_POLL_SECONDS,
    CollectorSettings,
    EngineCollector,
)

START_TS = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
GENERATION_LINE = "50 generated in 250 ms (1.0 tok/s)\n"


class FakeClock:
    """One batch per call: START_TS plus one second per batch."""

    def __init__(self) -> None:
        self.batches = 0

    def __call__(self) -> datetime:
        stamp = START_TS.replace(second=self.batches)
        self.batches += 1
        return stamp


class RecordingConn:
    """Fake connection: records the insert parameters; the first fail_count
    execute calls raise, or the call number fail_at raises once, to force the
    collector's retry path mid-batch."""

    def __init__(self, fail_count: int = 0, fail_at: int = 0) -> None:
        self.rows: list[tuple[Any, ...]] = []
        self.fail_count = fail_count
        self.fail_at = fail_at
        self.calls = 0

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.calls += 1
        if self.fail_count > 0:
            self.fail_count -= 1
            raise ConnectionError("fake database down")
        if self.calls == self.fail_at:
            raise ConnectionError("fake database down")
        self.rows.append(params)


class SteppingSleep:
    """Fake sleep: records every requested delay, runs one file action per
    poll, and sets the stop event when the actions are exhausted."""

    def __init__(
        self,
    stop_event: threading.Event,
    actions: list[Callable[[], object]],
) -> None:
        self.stop_event = stop_event
        self.actions = actions
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        if self.actions:
            self.actions.pop(0)()
        else:
            self.stop_event.set()


def build_collector(
    log_path: Path,
    conn: RecordingConn,
    actions: list[Callable[[], object]],
    *,
    poll_seconds: float = 1.0,
    max_batch_lines: int = 100,
    max_db_retries: int = 0,
    backoff_seconds: float = 2.0,
    cooldown_seconds: float = 300.0,
) -> tuple[EngineCollector, SteppingSleep]:
    stop_event = threading.Event()
    settings = CollectorSettings(
        log_path=str(log_path),
        poll_seconds=poll_seconds,
        max_batch_lines=max_batch_lines,
        max_db_retries=max_db_retries,
        backoff_seconds=backoff_seconds,
        cooldown_seconds=cooldown_seconds,
    )
    sleeper = SteppingSleep(stop_event, actions)
    collector = EngineCollector(
        settings, conn, clock=FakeClock(), sleep=sleeper, stop_event=stop_event
    )
    return collector, sleeper


def test_append_partial_line_and_restart_marker(tmp_path: Path) -> None:
    """The restart line is read first; the generation line stays partial until
    its closing newline is appended on the next poll."""
    log_path = tmp_path / "engine.log"
    log_path.write_text("engine started: model A\n50 generated in 250 ms (1.0 tok/s")
    conn = RecordingConn()
    collector, sleeper = build_collector(
        log_path, conn, [lambda: log_path.open("a").write(")\n")]
    )
    collector.run()

    assert len(conn.rows) == 2
    restart_row, generation_row = conn.rows
    assert restart_row[0] == START_TS
    assert restart_row[1] == 1  # first 'engine started:' line opens engine_run 1
    assert generation_row[0] == START_TS.replace(second=1)  # second batch, own stamp
    assert generation_row[1] == 1
    assert generation_row[2] == 50
    assert generation_row[3] == 1.0
    assert collector.engine_run == 1
    assert collector.partial_line == ""
    assert collector.offset == log_path.stat().st_size
    assert len(sleeper.delays) == 2


def test_truncation_restarts_from_zero(tmp_path: Path) -> None:
    """A rewrite shrinking the log below the stored offset restarts the read
    from 0 and keeps counting engine_run from the earlier restart."""
    log_path = tmp_path / "engine.log"
    log_path.write_text("engine started: model A\n" + GENERATION_LINE)
    conn = RecordingConn()
    collector, sleeper = build_collector(
        log_path,
        conn,
        [
            lambda: log_path.open("w").write("engine started: model B\n"),
            lambda: log_path.open("a").write(GENERATION_LINE),
        ],
    )
    collector.run()

    engine_runs = [row[1] for row in conn.rows]
    assert engine_runs == [1, 1, 2, 2]
    assert len(conn.rows) == 4
    assert collector.offset == log_path.stat().st_size
    assert len(sleeper.delays) == 3


def test_second_restart_marker_increments_engine_run(tmp_path: Path) -> None:
    log_path = tmp_path / "engine.log"
    log_path.write_text("engine started: model A\nengine started: model B\n")
    conn = RecordingConn()
    collector, _ = build_collector(log_path, conn, [])
    collector.run()

    assert [row[1] for row in conn.rows] == [1, 2]
    assert collector.engine_run == 2


def test_max_batch_lines_caps_a_poll(tmp_path: Path) -> None:
    """Only max_batch_lines complete lines are consumed per poll; the rest are
    read on the next poll from the stored offset."""
    log_path = tmp_path / "engine.log"
    log_path.write_text(GENERATION_LINE * 4)
    conn = RecordingConn()
    collector, sleeper = build_collector(
        log_path, conn, [lambda: None], max_batch_lines=2
    )
    collector.run()

    assert len(conn.rows) == 4
    assert collector.offset == log_path.stat().st_size
    assert len(sleeper.delays) == 2


def test_database_error_is_retried_then_written(tmp_path: Path) -> None:
    """One failing write is retried after backoff_seconds and succeeds; the
    batch timestamp is still the injected clock's value."""
    log_path = tmp_path / "engine.log"
    log_path.write_text("engine started: model A\n" + GENERATION_LINE)
    conn = RecordingConn(fail_count=1)
    collector, sleeper = build_collector(
        log_path, conn, [lambda: None], poll_seconds=1.0, max_db_retries=3, backoff_seconds=4.0
    )
    collector.run()

    assert len(conn.rows) == 2
    assert sleeper.delays == [4.0, 1.0]  # retry backoff, then the poll delay
    assert conn.rows[0][0] == START_TS


def test_engine_run_not_double_counted_on_retry(tmp_path: Path) -> None:
    """A batch that fails on its second insert and is retried must not bump
    engine_run again for the same 'engine started:' line already inserted."""
    log_path = tmp_path / "engine.log"
    log_path.write_text("engine started: model A\n" + GENERATION_LINE)
    conn = RecordingConn(fail_at=2)
    collector, sleeper = build_collector(
        log_path, conn, [lambda: None], max_db_retries=2, backoff_seconds=4.0
    )
    collector.run()

    assert conn.rows  # the retried attempt records the restart line again
    assert all(row[1] == 1 for row in conn.rows)
    assert collector.engine_run == 1
    assert sleeper.delays == [4.0, 1.0]


def test_retries_exhausted_waits_then_writes_the_same_batch_again(tmp_path: Path) -> None:
    """When the retries are spent the loop cools down and writes the batch again."""
    log_path = tmp_path / "engine.log"
    log_path.write_text("engine started: model A\n" + GENERATION_LINE)
    conn = RecordingConn(fail_count=3)  # three attempts (retries 2 + 1) fail, the fourth works
    collector, sleeper = build_collector(
        log_path,
        conn,
        [lambda: None, lambda: None, lambda: None],
        max_db_retries=2,
        backoff_seconds=4.0,
        cooldown_seconds=30.0,
    )
    collector.run()

    assert sleeper.delays[:3] == [4.0, 4.0, 30.0]  # two retries, then the cooldown
    assert len(conn.rows) >= 2  # the restart row and the generation row, written after the cooldown
    assert collector.engine_run == 1


def test_stop_event_ends_the_loop_without_reading(tmp_path: Path) -> None:
    log_path = tmp_path / "engine.log"
    log_path.write_text("engine started: model A\n")
    conn = RecordingConn()
    stop_event = threading.Event()
    stop_event.set()
    settings = CollectorSettings(
        log_path=str(log_path),
        poll_seconds=1.0,
        max_batch_lines=100,
        max_db_retries=1,
        backoff_seconds=1.0,
    )
    delays: list[float] = []
    collector = EngineCollector(
        settings, conn, clock=FakeClock(), sleep=lambda s: delays.append(s), stop_event=stop_event
    )
    collector.run()

    assert conn.rows == []
    assert delays == []


def test_sample_keys_are_unique(tmp_path: Path) -> None:
    """Every row carries a sample_key so a repeated read cannot duplicate it;
    the keys of one batch are distinct."""
    log_path = tmp_path / "engine.log"
    log_path.write_text(
        "engine started: model A\n"
        "120 generated in 800 ms (1.5 tok/s), drafts accepted 60 of 120\n"
        "expert cache 8192 slots\n"
        "only 128 MiB of VRAM free\n"
    )
    conn = RecordingConn()
    collector, _ = build_collector(log_path, conn, [])
    collector.run()

    keys = {row[10] for row in conn.rows}
    # 4 lines, 6 samples: the generation line carries Generation and
    # DraftsAccepted, the warning line VramFree and LowVramWarning (engine_log)
    assert len(conn.rows) == 6
    assert len(keys) == 6
    assert all(row[9] == "sampled" for row in conn.rows)
    assert conn.rows[5][7] == 128 and conn.rows[5][8] is True  # low_vram flag set
    assert conn.rows[5][1] == 1  # the warning row carries the current run


def test_from_env_reads_the_dashboard_settings_and_keeps_the_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the settings the operator sets are read; the rest stay at their
    documented defaults, so the collector is configurable from the container."""
    monkeypatch.setenv(
        "DASHBOARD_ENGINE_LOG_PATH", "/strata-data/state/strata/launch/docker.engine.log"
    )
    monkeypatch.setenv("DASHBOARD_ENGINE_POLL_SECONDS", "30")
    monkeypatch.setenv("DASHBOARD_ENGINE_MAX_BATCH_LINES", "50")
    settings = CollectorSettings.from_env()
    assert settings.log_path == "/strata-data/state/strata/launch/docker.engine.log"
    assert settings.poll_seconds == 30.0
    assert settings.max_batch_lines == 50
    assert settings.max_db_retries == DEFAULT_MAX_DB_RETRIES
    assert settings.backoff_seconds == DEFAULT_BACKOFF_SECONDS


def test_from_env_keeps_defaults_for_a_bad_or_missing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-number or a non-positive setting is logged and the default stays:
    the collector never runs with a zero poll interval or a zero batch cap."""
    monkeypatch.setenv("DASHBOARD_ENGINE_POLL_SECONDS", "abc")
    monkeypatch.setenv("DASHBOARD_ENGINE_MAX_BATCH_LINES", "0")
    settings = CollectorSettings.from_env()
    assert settings.poll_seconds == DEFAULT_POLL_SECONDS
    assert settings.max_batch_lines == DEFAULT_MAX_BATCH_LINES
    assert settings.log_path == DEFAULT_ENGINE_LOG_PATH


def _collector_thread() -> threading.Thread | None:
    """The collector's thread, or None when the wiring did not start one."""
    return next((t for t in threading.enumerate() if t.name == "engine-collector"), None)


def _app_with(store: Any) -> Any:
    """A minimal app standing in for the dashboard app for the wiring tests."""
    return types.SimpleNamespace(state=types.SimpleNamespace(board_store=store))


class _FlaggedStore(InMemoryBoardStore):
    """In-memory board store carrying a recording connection and the
    stats_engine_sampler feature flag: the wiring tests need no database."""

    def __init__(self, *, flag_on: bool) -> None:
        super().__init__()
        self.conn = RecordingConn()
        self._conn = self.conn
        self.update_hermes_config(
            None,
            features_json=json.dumps({"stats_engine_sampler": {"enabled": flag_on}}),
        )


class _UnreadableFlagsStore(InMemoryBoardStore):
    """A store whose feature flags cannot be read: the wiring must not raise."""

    def hermes_config(self) -> dict[str, Any]:
        raise ConnectionError("hermes_config row unreadable")


def test_collector_wiring_is_off_when_the_flag_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flag off: no thread, nothing read, the dashboard keeps its old behaviour."""
    log_path = tmp_path / "engine.log"
    log_path.write_text(GENERATION_LINE)
    monkeypatch.setenv("DASHBOARD_ENGINE_LOG_PATH", str(log_path))
    store = _FlaggedStore(flag_on=False)
    stop = board_api.start_engine_collector(_app_with(store))
    assert _collector_thread() is None
    assert store.conn.rows == []
    stop.set()


def test_collector_wiring_starts_with_the_flag_and_stops_on_the_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flag on: the thread reads the staged log once, writes samples, and the
    stop event ends its bounded poll loop."""
    log_path = tmp_path / "engine.log"
    log_path.write_text("engine started: model A\n" + GENERATION_LINE)
    monkeypatch.setenv("DASHBOARD_ENGINE_LOG_PATH", str(log_path))
    monkeypatch.setenv("DASHBOARD_ENGINE_POLL_SECONDS", "0.1")
    store = _FlaggedStore(flag_on=True)
    stop = board_api.start_engine_collector(_app_with(store))
    thread = _collector_thread()
    assert thread is not None
    for _ in range(20):  # bounded wait: 20 checks of 0.1 s at most
        if store.conn.rows:
            break
        time.sleep(0.1)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(store.conn.rows) == 2
    assert store.conn.rows[0][1] == 1  # engine_run of the restart marker row
    assert store.conn.rows[1][2] == 50  # generated_tokens of the generation row
    assert all(row[9] == "sampled" for row in store.conn.rows)

def test_collector_wiring_does_not_start_without_a_database_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No DSN behind the board store: the wiring returns a stop event and starts
    no thread; the dashboard does not crash and the engine block stays null."""
    monkeypatch.setenv("DASHBOARD_ENGINE_LOG_PATH", "/tmp/does-not-matter/engine.log")
    for store in (UnavailableBoardStore("no DSN set"), InMemoryBoardStore()):
        stop = board_api.start_engine_collector(_app_with(store))
        assert _collector_thread() is None
        stop.set()


def test_collector_wiring_survives_unreadable_feature_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store that cannot read its feature flags logs the reason and starts no
    collector: the dashboard keeps serving without engine samples."""
    monkeypatch.setenv("DASHBOARD_ENGINE_LOG_PATH", "/tmp/does-not-matter/engine.log")
    stop = board_api.start_engine_collector(_app_with(_UnreadableFlagsStore()))
    assert _collector_thread() is None
    stop.set()
