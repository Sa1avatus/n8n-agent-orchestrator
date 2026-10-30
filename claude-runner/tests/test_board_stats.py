"""Tests for board_stats.py (the mission card and the per-task / per-attempt
figures): the aggregates over synthetic runs (time, tokens, cache, thinking
on/off, cost, speed) and the run-to-task linkage through the event log.  The
fixtures mirror ``test_mission_stats.py`` (a fresh ``InMemoryBoardStore``, a
``RunManager`` built with explicit local pricing)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import pytest

from claude_runner.board import InMemoryBoardStore, Mission
from claude_runner.board_stats import mission_card_stats, task_stats
from claude_runner.config import Settings
from claude_runner.events import EventLog
from claude_runner.runs import RunManager
from claude_runner.store import Store

PRICING_ENV = {
    "CLAUDE_RUNNER_LOCAL_PRICE_SONNET": "2,10,0.2,4",
    "CLAUDE_RUNNER_LOCAL_PRICE_HAIKU": "1,5,0.1,1.25",
    "CLAUDE_RUNNER_LOCAL_PRICE_THINKING_OFF": "haiku",
    "CLAUDE_RUNNER_LOCAL_PRICE_THINKING_ON": "sonnet",
}


@pytest.fixture
def board() -> InMemoryBoardStore:
    """A fresh in-memory store (no seed: put rows in per test)."""
    return InMemoryBoardStore()


@pytest.fixture
def runs_store(settings) -> Store:
    return Store(settings.data_dir / "board_stats.sqlite")


@pytest.fixture
def run_manager(settings, runs_store) -> RunManager:
    env = {
        "CLAUDE_RUNNER_DATA_DIR": str(settings.data_dir),
        "CLAUDE_RUNNER_CLAUDE_BIN": settings.claude_bin,
        "CLAUDE_RUNNER_WORKSPACE": str(settings.workspace),
        "CLAUDE_RUNNER_PATH_MAP": ";".join(
            f"{src_path}={dst_path}" for src_path, dst_path in settings.path_map
        ),
        "CLAUDE_RUNNER_MAX_CONCURRENT": "2",
        "CLAUDE_RUNNER_COMPACT_MIN_TOKENS": "2500",
        **PRICING_ENV,
    }
    return RunManager(Settings.from_env(env), runs_store)


def _seed_board(board: InMemoryBoardStore) -> None:
    """The mission row and the plan; the store updates rows but has no create,
    so the rows are put in directly."""
    board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1",
                                    name="Mission")
    board.upsert_live_state(
        "sk1", None,
        plan={"tasks": [{"id": "T001"}, {"id": "T002"}]}, status="queued",
    )


def _event(state_key: str, **fields: Any) -> dict:
    row = {"state_key": state_key, "created_at": "2026-01-01T00:00:00+00:00"}
    row.update(fields)
    return row


def _add_run(
    store: Store, run_id: str, session_id: str, role: str,
    started: datetime, finished: datetime | None,
    cost_usd: float, provider: str = "", details: dict | None = None,
    kind: str = "run",
) -> None:
    store.create_run(
        run_id=run_id, session_id=session_id, claude_session_id=run_id,
        role=role, model="m", provider=provider, cwd="/tmp", input="x",
        kind=kind, status="queued", cost_usd=cost_usd,
        details=json.dumps(details or {}),
        started_at=started.isoformat(),
        finished_at=finished.isoformat() if finished else None,
    )
    if finished:
        store.update_run(run_id, status="completed")


def _write_run_events(log: EventLog, run_id: str, entries: list[dict]) -> None:
    """Write a run's event log (the entries the stats read)."""
    log.open(run_id)
    for entry in entries:
        log.add(run_id, entry)
    log.close(run_id)


class TestBoardStats:
    """The mission card's aggregates over synthetic runs: time, tokens,
    cache, thinking on/off, cost (local and provider), speed."""

    def _link(self, board: InMemoryBoardStore, store: Store, manager: RunManager) -> None:
        """Two worker runs, one reviewer run, one compaction of the same
        session, and the event rows that link them to task T001."""
        base = datetime(2026, 1, 1, 10, 0)
        _add_run(store, "run_w1", "s1", "worker", base,
                 base + timedelta(minutes=10), 2.0, provider="local")
        _add_run(store, "run_w2", "s1", "worker",
                 base + timedelta(minutes=20), base + timedelta(minutes=25),
                 1.0, provider="local")
        _add_run(store, "run_r1", "s2", "reviewer",
                 base + timedelta(minutes=30), base + timedelta(minutes=35), 0.5)
        _add_run(store, "run_c1", "s1", "compact", base,
                 base + timedelta(minutes=1), 0.0, kind="compact")
        _write_run_events(manager.events, "run_c1", [
            {"kind": "compact", "session_id": "s1"},
        ])
        _write_run_events(manager.events, "run_w1", [
            {"kind": "thinking_state", "t": 100, "thinking": "on"},
            {"kind": "step", "step": 1, "started": 110, "input_tokens": 100_000,
             "output_tokens": 10_000, "cache_read_tokens": 0,
             "cache_write_tokens": 0, "generation_ms": 1000},
            {"kind": "thinking_switch", "t": 200, "thinking": "off"},
            {"kind": "step", "step": 2, "started": 210, "input_tokens": 100_000,
             "output_tokens": 10_000, "cache_read_tokens": 0,
             "cache_write_tokens": 0, "generation_ms": 1000},
        ])
        _write_run_events(manager.events, "run_w2", [
            {"kind": "step", "step": 1, "started": 100, "input_tokens": 100_000,
             "output_tokens": 10_000, "cache_read_tokens": 20_000,
             "cache_write_tokens": 0, "generation_ms": 1000},
            {"kind": "step", "step": 2, "started": 200, "input_tokens": 100_000,
             "output_tokens": 10_000, "cache_read_tokens": 30_000,
             "cache_write_tokens": 0, "generation_ms": 1000},
        ])
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="worker_completed", worker_run_id="run_w1",
            worker_status="completed",
        ))
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="review_parsed", worker_run_id="run_w1",
            review_status="needs_changes", review_score=55.0,
            review_reason="not enough", reviewer_run_id="run_r1",
            reviewer_status="completed",
        ))
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=1,
            event_type="worker_completed", worker_run_id="run_w2",
            worker_status="completed",
        ))
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=1,
            event_type="review_parsed", worker_run_id="run_w2",
            review_status="pass", review_score=90.0, review_reason="ok",
            reviewer_run_id="run_r1", reviewer_status="completed",
        ))

    def test_aggregates_of_synthetic_runs(self, board, run_manager):
        """Time, tokens, cache, cost, speed and thinking of the linked runs."""
        _seed_board(board)
        store = run_manager.store
        self._link(board, store, run_manager)
        stats = mission_card_stats(run_manager, board, "sk1")

        # time: worker 10 + 5 min = 0.25 h; reviewer 5 min
        assert stats["runs"] == 4  # the linked runs plus the session's compaction
        assert stats["worker_hours"] == pytest.approx(0.25, abs=0.01)
        assert stats["reviewer_minutes"] == 5
        assert stats["compactions"] == 1
        # the compaction of the worker session is counted too (time and money)
        store.update_run("run_c1", cost_usd=0.05)
        stats = mission_card_stats(run_manager, board, "sk1")
        assert stats["runs"] == 4
        assert stats["cost_estimate_usd"] == pytest.approx(3.55)

        # tokens and cache
        assert stats["input_tokens"] == 400_000
        assert stats["output_tokens"] == 40_000
        assert stats["cache_read_tokens"] == 50_000
        assert stats["cache_ratio"] == pytest.approx(50_000 / 450_000, abs=0.001)

        # speed: 40k output tokens in 4 s
        assert stats["tok_per_s"] == pytest.approx(10_000)

        # thinking: run_w1's first step started while on was in force, the
        # second after the switch; run_w2 has no mode entries and defaults to on
        # (the sums count input + output + cache_read + cache_write per step)
        assert stats["thinking_runs"] == 3
        assert stats["thinking_off_runs"] == 1
        assert stats["thinking_steps"] == 4
        # on: 3 steps x (100k + 10k + 20k + 30k); off: 1 step x (100k + 10k)
        assert stats["thinking_tokens"] == 380_000
        assert stats["thinking_off_tokens"] == 110_000

        # cost: for a local run the runner's own price stands in for the stored
        # one (the local model costs nothing per token; the run is priced as
        # its cloud equivalent), the non-local runs keep the provider-reported
        # cost.  run_w1: off step 0.1M in @1 + 0.01M out @5 = 0.15,
        # on step 0.1M in @2 + 0.01M out @10 = 0.30;  run_w2 has no mode
        # entries, both steps default to on:
        # 0.1M in @2 + 0.01M out @10 + 0.02M cache_read @0.2 = 0.304,
        # 0.1M in @2 + 0.01M out @10 + 0.03M cache_read @0.2 = 0.306;
        # run_r1 (non-local): 0.5; run_c1 (no provider set): 0.05
        assert stats["cost_usd"] == pytest.approx(0.45 + 0.61 + 0.5 + 0.05, abs=0.01)
        # cost_real: only the non-local runs' provider-reported cost
        assert stats["cost_real_usd"] == pytest.approx(0.55)
        # local_cost_usd: the runner's own price of the whole mission (local
        # runs priced as their cloud equivalent, non-local at the reported cost)
        assert stats["local_cost_usd"] == pytest.approx(1.61, abs=0.01)

        # task T001: the last review is the pass of the second attempt
        assert stats["tasks"]["T001"] == {
            "attempts": 2, "last_review_score": 90.0,
            "last_review_reason": "ok",
        }
        assert stats["max_attempts_per_task"] is None
        # a task without any event row still appears in the card (the plan has
        # it) and carries zero attempts
        assert stats["tasks"]["T002"] == {
            "attempts": 0, "last_review_score": None, "last_review_reason": None,
        }

    def test_a_run_in_neither_the_live_row_nor_the_events_is_not_counted(
        self, board, run_manager
    ):
        _seed_board(board)
        store = run_manager.store
        self._link(board, store, run_manager)
        base = datetime(2026, 1, 1, 10, 0)
        _add_run(store, "run_x", "s9", "worker", base,
                 base + timedelta(minutes=1), 1.0)
        stats = mission_card_stats(run_manager, board, "sk1")
        assert stats["runs"] == 4
        assert stats["cost_estimate_usd"] == pytest.approx(3.5)


class TestTaskStats:
    """The per-task figures: the task's runs and its per-attempt time."""

    def _link(self, board: InMemoryBoardStore, store: Store, manager: RunManager) -> None:
        """One worker run and one reviewer run, linked to task T001."""
        base = datetime(2026, 1, 1, 10, 0)
        _add_run(store, "run_w1", "s1", "worker", base,
                 base + timedelta(minutes=5), 2.0)
        _add_run(store, "run_r1", "s2", "reviewer",
                 base + timedelta(minutes=5), base + timedelta(minutes=6), 0.5)
        _write_run_events(manager.events, "run_w1", [
            {"kind": "thinking_state", "t": 100, "thinking": "on"},
            {"kind": "step", "step": 1, "started": 110, "input_tokens": 1_000_000,
             "output_tokens": 100_000, "cache_read_tokens": 0,
             "cache_write_tokens": 0, "generation_ms": 2000},
            {"kind": "thinking_switch", "t": 200, "thinking": "off"},
            {"kind": "step", "step": 2, "started": 210, "input_tokens": 500_000,
             "output_tokens": 50_000, "cache_read_tokens": 100_000,
             "cache_write_tokens": 0, "generation_ms": 1000},
        ])
        _write_run_events(manager.events, "run_r1", [
            {"kind": "step", "step": 1, "started": 100, "input_tokens": 10_000,
             "output_tokens": 1_000, "cache_read_tokens": 0,
             "cache_write_tokens": 0, "generation_ms": 500},
        ])
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="worker_completed", worker_run_id="run_w1",
            worker_status="completed",
        ))
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="review_parsed", worker_run_id="run_w1",
            review_status="needs_changes", review_score=55.0,
            review_reason="not enough", reviewer_run_id="run_r1",
            reviewer_status="completed",
        ))

    def test_task_stats_of_the_linked_runs(self, board, run_manager):
        """The task's aggregates: time, tokens, cache, speed, thinking, cost."""
        _seed_board(board)
        store = run_manager.store
        self._link(board, store, run_manager)
        stats = task_stats(run_manager, board, "sk1", "T001")

        assert stats["mission_id"] == "m1"
        assert stats["task_id"] == "T001"
        # the task's runs are run_w1 and run_r1 (linked through the event rows)
        assert stats["runs"] == 2
        # time: worker 5 min; reviewer 1 min
        assert stats["worker_hours"] == pytest.approx(5 / 60, abs=0.01)
        assert stats["reviewer_minutes"] == 1
        # tokens and cache
        assert stats["input_tokens"] == 1_510_000
        assert stats["output_tokens"] == 151_000
        assert stats["cache_read_tokens"] == 100_000
        assert stats["cache_write_tokens"] == 0
        assert stats["cache_ratio"] == pytest.approx(100_000 / 1_610_000, abs=0.001)
        # speed: the worker steps of these non-local runs count as cloud speed
        assert stats["tok_per_s_local"] == 0.0
        assert stats["tok_per_s_cloud"] == stats["tok_per_s"] == 50000.0
        # thinking: run_w1's first step started while on was in force, the
        # second after the switch; run_r1 has no mode entries and defaults to on
        assert stats["thinking_runs"] == 2
        assert stats["thinking_off_runs"] == 1
        assert stats["thinking_steps"] == 3
        # on: (1_000k + 100k) + (10k + 1k); off: (500k + 50k + 100k)
        assert stats["thinking_tokens"] == 1_111_000
        assert stats["thinking_off_tokens"] == 650_000
        # cost: run_w1 (non-local, provider-reported 2.0) + run_r1 (0.5)
        assert stats["cost_usd"] == pytest.approx(2.5)
        assert stats["cost_real_usd"] == pytest.approx(2.5)
        assert stats["cost_estimate_usd"] == pytest.approx(2.5)
        # the last review is the needs_changes of the first attempt
        assert stats["attempts"] == 1
        assert stats["last_review_score"] == 55.0
        assert stats["last_review_reason"] == "not enough"

        # a task without any event row has empty figures and no attempts
        empty = task_stats(run_manager, board, "sk1", "T002")
        assert empty["runs"] == 0
        assert empty["attempts"] == 0
        assert empty["attempt_seconds"] == []
        assert empty["cost_estimate_usd"] == 0.0
        assert empty["last_review_score"] is None

    def test_task_stats_with_per_attempt_time(self, board, run_manager):
        """Two attempts with a shared reviewer run (counted once, on the
        first attempt)."""
        _seed_board(board)
        store = run_manager.store
        base = datetime(2026, 1, 1, 10, 0)
        _add_run(store, "run_w1", "s1", "worker", base,
                 base + timedelta(minutes=5), 2.0)
        _add_run(store, "run_r1", "s2", "reviewer",
                 base + timedelta(minutes=5), base + timedelta(minutes=6), 0.5)
        _add_run(store, "run_w2", "s1", "worker", base + timedelta(minutes=7),
                 base + timedelta(minutes=12), 3.0)
        _write_run_events(run_manager.events, "run_w1", [
            {"kind": "step", "step": 1, "started": 100, "input_tokens": 1_000_000,
             "output_tokens": 100_000, "cache_read_tokens": 0,
             "cache_write_tokens": 0, "generation_ms": 1000},
        ])
        _write_run_events(run_manager.events, "run_w2", [
            {"kind": "step", "step": 1, "started": 100, "input_tokens": 1_000_000,
             "output_tokens": 100_000, "cache_read_tokens": 0,
             "cache_write_tokens": 0, "generation_ms": 1000},
        ])
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="worker_completed", worker_run_id="run_w1",
            worker_status="completed",
        ))
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="review_parsed", worker_run_id="run_w1",
            review_status="needs_changes", review_score=55.0,
            review_reason="not enough", reviewer_run_id="run_r1",
            reviewer_status="completed",
        ))
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=1,
            event_type="worker_completed", worker_run_id="run_w2",
            worker_status="completed",
        ))
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=1,
            event_type="review_parsed", worker_run_id="run_w2",
            review_status="pass", review_score=90.0, review_reason="ok",
            reviewer_run_id="run_r1", reviewer_status="completed",
        ))
        stats = task_stats(run_manager, board, "sk1", "T001")

        # the task's runs: run_w1, run_w2 and the shared reviewer run
        assert stats["runs"] == 3
        # worker work: 5 + 5 min = 0.167 h; reviewer 1 min
        assert stats["worker_hours"] == pytest.approx(0.167, abs=0.01)
        assert stats["reviewer_minutes"] == 1
        assert stats["cost_estimate_usd"] == pytest.approx(5.5)
        # per attempt: the attempt's worker run plus its reviewer run (the
        # shared reviewer run is counted once, on the first attempt)
        assert stats["attempts"] == 2
        assert stats["attempt_seconds"] == [pytest.approx(360.0), pytest.approx(300.0)]
        assert stats["attempts_detail"][0] == {
            "attempt": 0, "seconds": pytest.approx(360.0), "runs": ["run_r1", "run_w1"]
        }
        assert stats["attempts_detail"][1] == {
            "attempt": 1, "seconds": pytest.approx(300.0), "runs": ["run_r1", "run_w2"]
        }
        # no thinking entries: every step is counted as thinking on
        assert stats["thinking_runs"] == 2
        assert stats["thinking_off_runs"] == 0
        assert stats["thinking_tokens"] == 2_200_000
        assert stats["max_attempts_per_task"] is None
        assert stats["last_review_score"] == 90.0
        assert stats["last_review_reason"] == "ok"

        # a task without any event row has empty figures and no attempts
        empty = task_stats(run_manager, board, "sk1", "T002")
        assert empty["runs"] == 0
        assert empty["attempts"] == 0
        assert empty["attempt_seconds"] == []
        assert empty["cost_estimate_usd"] == 0.0
        assert empty["last_review_score"] is None


class TestRunLinkage:
    """The mission's runs are found through the event log's ``worker_run_id``
    / ``reviewer_run_id`` rows (ids that are not ``run_``-shaped are kept out)."""

    def _board_with_runs(self, board: InMemoryBoardStore, manager: RunManager) -> None:
        board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1",
                                        name="Mission")
        board.upsert_live_state(
            "sk1", None,
            plan={"tasks": [{"id": "T001"}]}, status="queued",
        )
        store = manager.store
        base = datetime(2026, 1, 1, 10, 0)
        _add_run(store, "run_w1", "s1", "worker", base,
                 base + timedelta(minutes=1), 1.0)
        _add_run(store, "run_bad", "s1", "worker", base,
                 base + timedelta(minutes=1), 1.0)
        # a compaction of the session: the aggregated figures count it (as the
        # mission card does), while the per-attempt run lists stay explicit
        _add_run(store, "run_c1", "s1", "compact", base,
                 base + timedelta(minutes=1), 0.0, kind="compact")

    def test_links_runs_to_the_task_through_the_events(self, board, run_manager):
        self._board_with_runs(board, run_manager)
        # run_w1 and run_bad both start with ``run_`` and are in the event rows
        # (so they both count); the session's compaction is counted in the
        # aggregates too (like in the mission card), while ``not_a_run_id`` is
        # kept out of both the run list and the attempt's run list
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="worker_completed", worker_run_id="run_w1",
            worker_status="completed",
        ))
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="review_parsed", worker_run_id="run_bad",
            review_status="pass", review_score=90.0, review_reason="ok",
            reviewer_run_id="not_a_run_id", reviewer_status="completed",
        ))
        stats = task_stats(run_manager, board, "sk1", "T001")
        assert stats["runs"] == 3
        assert stats["attempts_detail"][0]["runs"] == ["run_bad", "run_w1"]
        # the non-run id is neither counted as a run nor as an attempt
        assert stats["attempts"] == 1

    def test_live_state_row_also_carries_the_run_ids(self, board, run_manager):
        self._board_with_runs(board, run_manager)
        # the run id is on the Live State row only: it counts as the mission's
        # run even without event rows
        board.upsert_live_state("sk1", None, plan={"tasks": [{"id": "T001"}]},
                               status="active", worker_run_id="run_w1",
                               worker_status="completed")
        stats = mission_card_stats(run_manager, board, "sk1")
        # the session's compaction counts in the aggregates too (as the
        # mission card does), so the mission has two runs, not one
        assert stats["runs"] == 2

    def test_queue_override_of_max_attempts_per_task(self, board, run_manager):
        """The queue entry's ``attempts_per_task`` overrides the config's value."""
        _seed_board(board)
        store = run_manager.store
        base = datetime(2026, 1, 1, 10, 0)
        _add_run(store, "run_w1", "s1", "worker", base,
                 base + timedelta(minutes=5), 2.0)
        board.add_queue("m1", None, attempts_per_task=4)
        board.update_hermes_config(None, profile_id="claude-code",
                                  max_attempts_per_task=2)
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="worker_completed", worker_run_id="run_w1",
            worker_status="completed",
        ))
        stats = mission_card_stats(run_manager, board, "sk1")
        # the queue override wins over the hermes config's 2
        assert stats["max_attempts_per_task"] == 4
        stats = task_stats(run_manager, board, "sk1", "T001")
        assert stats["max_attempts_per_task"] == 4


class TestRoleRows:
    """The per-role rows of the mission card: the QA run is linked through a
    ``qa_run_id`` event row, and the rows exist whether roles_json is set, absent or invalid."""

    def _qa_run(self, board: InMemoryBoardStore, store: Store) -> None:
        base = datetime(2026, 1, 1, 10, 0)
        _add_run(store, "run_q1", "s3", "qa", base,
                 base + timedelta(minutes=6), 0.4)
        board.append_event("sk1", _event("sk1", qa_run_id="run_q1"))

    def test_qa_row_with_roles_json_shows_the_configured_model(self, board, run_manager):
        _seed_board(board)
        board.set_mission_config("m1", {"roles_json": json.dumps(
            {"qa": {"1": "anthropic/haiku", "options": {"mode": "final"}}})}, None)
        self._qa_run(board, run_manager.store)
        rows = mission_card_stats(run_manager, board, "sk1")["roles"]
        assert rows["qa"] == {
            "label": "QA", "runs": 1, "minutes": 6.0, "cost_usd": 0.4,
            "model": "anthropic/haiku",
        }
        assert rows["worker"]["runs"] == 0
        assert rows["worker"]["model"] is None

    def test_qa_row_without_roles_json_uses_the_default_model(self, board, run_manager):
        _seed_board(board)
        self._qa_run(board, run_manager.store)
        rows = mission_card_stats(run_manager, board, "sk1")["roles"]
        assert rows["qa"]["label"] == "QA"
        assert rows["qa"]["runs"] == 1
        assert rows["qa"]["model"] == "anthropic/sonnet"

    def test_invalid_roles_json_counts_as_no_roles(self, board, run_manager):
        _seed_board(board)
        board.set_mission_config("m1", {"roles_json": "{not json"}, None)
        self._qa_run(board, run_manager.store)
        rows = mission_card_stats(run_manager, board, "sk1")["roles"]
        assert rows["qa"]["runs"] == 1
        assert rows["qa"]["model"] == "anthropic/sonnet"
