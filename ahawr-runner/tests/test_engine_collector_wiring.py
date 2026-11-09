"""Dashboard start-up wiring of the engine collector (ahawr_runner.dashboard lifespan).

Every test drives the real start-up path: ``create_dashboard_app`` plus a
``TestClient`` context, so the collector is started and stopped by the
dashboard's own lifespan, never by a hand-made app.  Flag off: no thread.  Flag
on with a database that cannot be reached: the dashboard still answers and the
reason is logged.  Flag on with a connection whose writes fail: the bounded
retries are spent, the thread exits, the dashboard keeps serving.  Leaving the
client context sets the stop event, which ends the thread.  No real database is
touched: the flagged store with a recording fake connection is injected through
``board_api.get_board_store``.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ahawr_runner import board_api
from ahawr_runner.board import InMemoryBoardStore
from ahawr_runner.dashboard import DashboardSettings, create_dashboard_app

FIXTURE = Path(__file__).resolve().parents[1] / "qa" / "fixtures" / "default.json"
GENERATION_LINE = "engine started: model A\n50 generated in 250 ms (1.0 tok/s)\n"
UNREACHABLE_DSN = "postgresql://board@127.0.0.1:1/ahawr-board"  # port 1: refused
WAIT_SECONDS = 10.0  # bounded wait for a daemon collector thread to finish
POLL_TICK_SECONDS = 0.2  # bounded poll of the thread-alive check


def collector_thread() -> threading.Thread | None:
    """The collector thread, or None when the start-up wiring never made one."""
    return next((t for t in threading.enumerate() if t.name == "engine-collector"), None)


class RecordingConn:
    """Fake connection: records the insert parameters; fail=True raises on every
    execute so the collector's bounded retry path is the one that runs."""

    def __init__(self, fail: bool = False) -> None:
        self.rows: list[tuple[Any, ...]] = []
        self.fail = fail

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        if self.fail:
            raise ConnectionError("fake database down")
        self.rows.append(params)


class FlaggedStore(InMemoryBoardStore):
    """In-memory board store carrying the stats_engine_sampler flag and a fake
    connection: the wiring needs no database."""

    def __init__(self, *, flag_on: bool, conn: RecordingConn) -> None:
        super().__init__()
        self.conn = conn
        self._conn = conn
        self.update_hermes_config(
            None,
            features_json=json.dumps({"stats_engine_sampler": {"enabled": flag_on}}),
        )


def _dashboard_client() -> TestClient:
    """The plain dashboard app, the same one the container starts."""
    return TestClient(
        create_dashboard_app(DashboardSettings()), base_url="http://localhost:8701"
    )


def test_flag_off_through_the_real_startup_starts_no_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The QA fixture store carries the default flags (off): the lifespan makes no
    collector thread and the dashboard keeps answering."""
    monkeypatch.setenv("DASHBOARD_QA_FIXTURE", str(FIXTURE))
    monkeypatch.delenv("DASHBOARD_BOARD_PG_DSN", raising=False)
    monkeypatch.setenv("DASHBOARD_ENGINE_LOG_PATH", str(tmp_path / "docker.engine.log"))
    with _dashboard_client() as dash:
        assert dash.get("/healthz").status_code == 200
        assert collector_thread() is None
    assert collector_thread() is None


def test_flag_on_with_an_unreachable_database_does_not_crash_the_dashboard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A DSN that cannot be connected to leaves the board store unavailable: the
    lifespan logs the reason, starts no thread, and /healthz still answers."""
    monkeypatch.delenv("DASHBOARD_QA_FIXTURE", raising=False)
    monkeypatch.setenv("DASHBOARD_BOARD_PG_DSN", UNREACHABLE_DSN)
    monkeypatch.setenv("DASHBOARD_ENGINE_LOG_PATH", str(tmp_path / "docker.engine.log"))
    with caplog.at_level(logging.INFO), _dashboard_client() as dash:
        assert dash.get("/healthz").status_code == 200
        assert collector_thread() is None
    assert "engine collector not started: the board store is unavailable" in caplog.text


def test_flag_on_with_failing_writes_keeps_the_collector_waiting_and_the_dashboard_up(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Flag on, a connection that raises on every write: the retries are spent, the
    error is logged with the retry count, the thread waits for its cooldown and ends
    with the dashboard, and the dashboard answers."""
    log_path = tmp_path / "docker.engine.log"
    log_path.write_text(GENERATION_LINE)
    conn = RecordingConn(fail=True)
    monkeypatch.delenv("DASHBOARD_QA_FIXTURE", raising=False)
    monkeypatch.delenv("DASHBOARD_BOARD_PG_DSN", raising=False)
    monkeypatch.setattr(board_api, "get_board_store", lambda: FlaggedStore(flag_on=True, conn=conn))
    monkeypatch.setenv("DASHBOARD_ENGINE_LOG_PATH", str(log_path))
    monkeypatch.setenv("DASHBOARD_ENGINE_POLL_SECONDS", "0.1")
    monkeypatch.setenv("DASHBOARD_ENGINE_MAX_DB_RETRIES", "1")
    monkeypatch.setenv("DASHBOARD_ENGINE_BACKOFF_SECONDS", "0.1")
    with caplog.at_level(logging.INFO):
        with _dashboard_client() as dash:
            assert dash.get("/healthz").status_code == 200
            thread = collector_thread()
            assert thread is not None
            deadline = time.monotonic() + WAIT_SECONDS
            while "database write failed after 1 retries" not in caplog.text:
                assert time.monotonic() < deadline
                time.sleep(POLL_TICK_SECONDS)
            assert thread.is_alive()  # the outage does not end the collector
        thread.join(timeout=WAIT_SECONDS)
        assert not thread.is_alive()  # the shutdown ends the cooldown wait at once
    assert conn.rows == []


def test_shutdown_sets_the_stop_event_and_ends_the_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Flag on with a readable log and a recording connection: the collector samples
    while the client is open; leaving the context ends the thread and logs the stop."""
    log_path = tmp_path / "docker.engine.log"
    log_path.write_text(GENERATION_LINE)
    conn = RecordingConn()
    monkeypatch.delenv("DASHBOARD_QA_FIXTURE", raising=False)
    monkeypatch.delenv("DASHBOARD_BOARD_PG_DSN", raising=False)
    monkeypatch.setattr(board_api, "get_board_store", lambda: FlaggedStore(flag_on=True, conn=conn))
    monkeypatch.setenv("DASHBOARD_ENGINE_LOG_PATH", str(log_path))
    monkeypatch.setenv("DASHBOARD_ENGINE_POLL_SECONDS", "0.1")
    with caplog.at_level(logging.INFO):
        with _dashboard_client() as dash:
            assert dash.get("/healthz").status_code == 200
            thread = collector_thread()
            assert thread is not None
            deadline = time.monotonic() + WAIT_SECONDS
            while not conn.rows and time.monotonic() < deadline:
                time.sleep(POLL_TICK_SECONDS)
            assert conn.rows  # the staged log was sampled while the dashboard was up
            assert "engine collector started: polling" in caplog.text
        # leaving the client context ran the lifespan's engine_stop.set()
        thread.join(timeout=WAIT_SECONDS)
        assert not thread.is_alive()
        assert collector_thread() is None
        assert "engine collector stopped: offset" in caplog.text
