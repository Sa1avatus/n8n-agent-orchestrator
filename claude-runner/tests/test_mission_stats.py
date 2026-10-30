"""Mission and task statistics with run linkage (claude_runner/board_stats.py):
the mission card and the per-task / per-attempt figures, built on
``mission_stats.stats_for_runs``."""

import json
import os
import time
from datetime import datetime, timedelta
from typing import Any

import pytest

from claude_runner.board import InMemoryBoardStore, Mission, QueueEntry
from claude_runner.board_stats import mission_card_stats, task_stats
from claude_runner.config import Settings
from claude_runner.events import EventLog
from claude_runner.local_pricing import LocalPricing
from claude_runner.mission_stats import stats_for_runs
from claude_runner.runs import RunManager
from claude_runner.store import Store

PRICING_ENV = {
    "CLAUDE_RUNNER_LOCAL_PRICE_SONNET": "2,10,0.2,4",
    "CLAUDE_RUNNER_LOCAL_PRICE_HAIKU": "1,5,0.1,1.25",
    "CLAUDE_RUNNER_LOCAL_PRICE_THINKING_OFF": "haiku",
    "CLAUDE_RUNNER_LOCAL_PRICE_THINKING_ON": "sonnet",
}


def seed_mission(board: InMemoryBoardStore) -> None:
    """The store updates rows but has no create: put the mission row in directly."""
    board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1", name="Mission one")


@pytest.fixture
def runs(settings: Settings) -> Store:
    return Store(settings.data_dir / "test.sqlite")


@pytest.fixture
def manager(settings: Settings, runs: Store) -> RunManager:
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
    s = Settings.from_env(env)
    object.__setattr__(s, "interrupt_grace_seconds", 2.0)
    return RunManager(s, runs)


def add_run(
    store: Store,
    run_id: str,
    session_id: str,
    role: str,
    started: datetime,
    finished: datetime | None,
    cost_usd: float,
    provider: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    store.create_run(
        run_id=run_id,
        session_id=session_id,
        claude_session_id=run_id,
        role=role,
        model="m",
        provider=provider,
        cwd="/tmp",
        input="x",
        status="queued",
        cost_usd=cost_usd,
        details=json.dumps(details or {}),
        started_at=started.isoformat(),
        finished_at=finished.isoformat() if finished else None,
    )
    if finished:
        store.update_run(run_id, status="completed")


def write_events(log: EventLog, run_id: str, entries: list[dict[str, Any]]) -> None:
    log.open(run_id)
    for entry in entries:
        log.add(run_id, entry)
    log.close(run_id)


def mission_board(manager: RunManager, settings: Settings) -> InMemoryBoardStore:
    board = InMemoryBoardStore()
    seed_mission(board)
    board.update_hermes_config(None, profile_id="claude-code", max_attempts_per_task=2)
    board.add_queue("m1", None, attempts_per_task=4)
    return board


def event(state_key: str, **fields: Any) -> dict[str, Any]:
    row = {"state_key": state_key, "created_at": "2026-01-01T00:00:00+00:00"}
    row.update(fields)
    return row


# -------------------------------------------------------------------------- mission


def test_mission_card_stats_links_runs_through_the_event_log(
    manager: RunManager, settings: Settings
):
    board = mission_board(manager, settings)
    store = manager.store
    base = datetime(2026, 1, 1, 10, 0)
    add_run(store, "run_w1", "s1", "worker", base, base + timedelta(minutes=10), 2.0,
            provider="local")
    add_run(store, "run_w2", "s1", "worker", base + timedelta(minutes=20),
            base + timedelta(minutes=25), 1.0, provider="local")
    add_run(store, "run_r1", "s2", "reviewer", base + timedelta(minutes=30),
            base + timedelta(minutes=35), 0.5)
    add_run(store, "run_a1", "s3", "architect", base, base + timedelta(minutes=3),
            0.25)

    write_events(
        manager.events, "run_w1",
        [
            {"kind": "thinking_state", "t": 100, "thinking": "on"},
            {"kind": "step", "step": 1, "started": 110, "input_tokens": 1_000_000,
             "output_tokens": 100_000, "cache_read_tokens": 0, "cache_write_tokens": 0},
            {"kind": "thinking_switch", "t": 200, "thinking": "off"},
            {"kind": "step", "step": 2, "started": 210, "input_tokens": 1_000_000,
             "output_tokens": 100_000, "cache_read_tokens": 0, "cache_write_tokens": 0},
        ],
    )
    write_events(
        manager.events, "run_w2",
        [
            {"kind": "step", "step": 1, "started": 100, "input_tokens": 1_000_000,
             "output_tokens": 50_000, "cache_read_tokens": 200_000,
             "cache_write_tokens": 0},
            {"kind": "step", "step": 2, "started": 200, "input_tokens": 1_000_000,
             "output_tokens": 50_000, "cache_read_tokens": 300_000,
             "cache_write_tokens": 0},
        ],
    )

    board.append_event(
        "sk1",
        event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="worker_completed", worker_run_id="run_w1",
            worker_status="completed",
        ),
    )
    board.append_event(
        "sk1",
        event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="review_parsed", worker_run_id="run_w1",
            review_status="needs_changes", review_score=55.0,
            review_reason="not enough", reviewer_run_id="run_r1",
            reviewer_status="completed",
        ),
    )
    board.append_event(
        "sk1",
        event(
            "sk1", task_id="T001", task_index=0, task_attempt=1,
            event_type="worker_completed", worker_run_id="run_w2",
            worker_status="completed",
        ),
    )
    board.append_event(
        "sk1",
        event(
            "sk1", task_id="T001", task_index=0, task_attempt=1,
            event_type="review_parsed", worker_run_id="run_w2",
            review_status="pass", review_score=90.0,
            review_reason="ok", reviewer_run_id="run_r1",
            reviewer_status="completed", review_next_task="T002",
        ),
    )
    board.append_event(
        "sk1",
        event(
            "sk1", task_id="T002", task_index=1, task_attempt=0,
            event_type="worker_completed", worker_run_id="run_w1",
            worker_status="completed",
        ),
    )
    board.append_event(
        "sk1",
        event(
            "sk1", task_id="T002", task_index=1, task_attempt=0,
            event_type="reviewer_completed", reviewer_run_id="run_r1",
            reviewer_status="completed",
        ),
    )
    board.upsert_live_state(
        "sk1", None, plan={"tasks": [{"id": "T001"}, {"id": "T002"}]},
        task_index=1, task_attempt=0, status="active",
        worker_run_id="run_w2", worker_status="completed",
        reviewer_run_id="run_r1", reviewer_status="completed",
        architect_run_id="run_a1", architect_status="completed",
        last_review_score=90.0, last_review_reason="ok",
    )

    stats = mission_card_stats(manager, board, "sk1")
    assert stats["mission_id"] == "m1"
    assert stats["status"] == "active"
    # the mission's runs are the run ids of the Live State row and of the event rows
    assert stats["runs"] == 4
    # worker work: 10 min of run_w1 + 5 min of run_w2 = 0.25 h
    assert stats["worker_hours"] == pytest.approx(0.25, abs=0.01)
    assert stats["reviewer_minutes"] == 5
    assert stats["architect_minutes"] == 3
    assert stats["cost_estimate_usd"] == pytest.approx(3.75)
    # cost_real is the stored cost of the non-local runs (reviewer + architect)
    assert stats["cost_real_usd"] == pytest.approx(0.75)
    # the local price replaces the runner cost of the local runs:
    # run_w1: 1.0M in @1 + 0.1M out @5 = 1.5 (off) + 1.0M in @2 + 0.1M out @10 = 3 (on)
    # run_w2 has no mode entries, so both steps default to on (sonnet):
    #   1.0M in @2 + 0.05M out @10 + 0.2M cache_read @0.2 = 2.54,
    #   1.0M in @2 + 0.05M out @10 + 0.3M cache_read @0.2 = 2.56
    assert stats["cost_usd"] == pytest.approx(1.5 + 3 + 2.54 + 2.56 + 0.5 + 0.25, abs=0.01)
    assert stats["local_cost_usd"] is not None
    # thinking: run_w1's first step started while "on" was in force, the second after
    # the switch; run_w2 has no mode entries and defaults to on
    assert stats["thinking_runs"] == 3
    assert stats["thinking_off_runs"] == 1
    assert stats["thinking_steps"] == 4
    # on tokens: 1.1M (w1 s1) + 1.25M (w2 s1) + 1.35M (w2 s2); off: 1.1M (w1 s2)
    assert stats["thinking_tokens"] == 3_700_000
    assert stats["thinking_off_tokens"] == 1_100_000
    # attempts per task: distinct review rows of the task
    assert stats["tasks"]["T001"] == {
        "attempts": 2, "last_review_score": 90.0, "last_review_reason": "ok"
    }
    assert stats["tasks"]["T002"]["attempts"] == 1
    # the queue override wins over the hermes config's 2
    assert stats["max_attempts_per_task"] == 4
    # the run ids come from both the Live State row and the event rows;
    # a run that is in neither is not counted
    add_run(store, "run_x", "s9", "worker", base, base + timedelta(minutes=1), 1.0)
    stats = mission_card_stats(manager, board, "sk1")
    assert stats["runs"] == 4
    assert stats["cost_estimate_usd"] == pytest.approx(3.75)


def test_mission_card_stats_of_a_not_started_mission(manager: RunManager, settings: Settings):
    board = mission_board(manager, settings)
    stats = mission_card_stats(manager, board, "sk1")
    assert stats["status"] is None
    assert stats["runs"] == 0
    assert stats["worker_hours"] == 0.0
    assert stats["cost_estimate_usd"] == 0.0
    assert stats["cost_usd"] == 0.0
    assert stats["max_attempts_per_task"] == 4
    assert stats["tasks"] == {}


# --------------------------------------------------------------------------- tasks


def test_task_stats_with_per_attempt_time(manager: RunManager, settings: Settings):
    board = mission_board(manager, settings)
    store = manager.store
    base = datetime(2026, 1, 1, 10, 0)
    add_run(store, "run_w1", "s1", "worker", base, base + timedelta(minutes=5), 2.0)
    add_run(store, "run_r1", "s2", "reviewer", base + timedelta(minutes=5),
            base + timedelta(minutes=6), 0.5)
    add_run(store, "run_w2", "s1", "worker", base + timedelta(minutes=7),
            base + timedelta(minutes=12), 3.0)

    write_events(
        manager.events, "run_w1",
        [{"kind": "step", "step": 1, "started": 100, "input_tokens": 1_000_000,
          "output_tokens": 100_000, "cache_read_tokens": 0, "cache_write_tokens": 0}],
    )
    write_events(
        manager.events, "run_w2",
        [{"kind": "step", "step": 1, "started": 100, "input_tokens": 1_000_000,
          "output_tokens": 100_000, "cache_read_tokens": 0, "cache_write_tokens": 0}],
    )

    board.append_event(
        "sk1",
        event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="worker_completed", worker_run_id="run_w1",
            worker_status="completed",
        ),
    )
    board.append_event(
        "sk1",
        event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="review_parsed", worker_run_id="run_w1",
            review_status="needs_changes", review_score=55.0,
            review_reason="not enough", reviewer_run_id="run_r1",
            reviewer_status="completed",
        ),
    )
    board.append_event(
        "sk1",
        event(
            "sk1", task_id="T001", task_index=0, task_attempt=1,
            event_type="worker_completed", worker_run_id="run_w2",
            worker_status="completed",
        ),
    )
    board.append_event(
        "sk1",
        event(
            "sk1", task_id="T001", task_index=0, task_attempt=1,
            event_type="review_parsed", worker_run_id="run_w2",
            review_status="pass", review_score=90.0, review_reason="ok",
            reviewer_run_id="run_r1", reviewer_status="completed",
        ),
    )
    board.upsert_live_state(
        "sk1", None, plan={"tasks": [{"id": "T001"}, {"id": "T002"}]},
        task_index=0, task_attempt=1, status="active",
        worker_run_id="run_w2", worker_status="completed",
        reviewer_run_id="run_r1", reviewer_status="completed",
        last_review_score=90.0, last_review_reason="ok",
    )

    stats = task_stats(manager, board, "sk1", "T001")
    assert stats["mission_id"] == "m1"
    assert stats["task_id"] == "T001"
    assert stats["runs"] == 3
    assert stats["worker_hours"] == pytest.approx(0.167, abs=0.01)  # 5 + 5 min of the two attempts
    assert stats["reviewer_minutes"] == 1
    assert stats["cost_estimate_usd"] == pytest.approx(5.5)
    # per attempt: the attempt's worker run plus its reviewer run
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
    assert stats["max_attempts_per_task"] == 4
    assert stats["last_review_score"] == 90.0
    assert stats["last_review_reason"] == "ok"

    # a task without any event row has empty figures and no attempts
    empty = task_stats(manager, board, "sk1", "T002")
    assert empty["runs"] == 0
    assert empty["attempts"] == 0
    assert empty["attempt_seconds"] == []
    assert empty["cost_estimate_usd"] == 0.0
    assert empty["last_review_score"] is None


def test_speed_is_split_between_the_local_and_the_cloud_provider(manager: RunManager) -> None:
    base = datetime(2026, 1, 1, 10, 0)
    add_run(manager.store, "run_loc", "s1", "worker", base, base + timedelta(minutes=5), 0.0,
            provider="local")
    add_run(manager.store, "run_cld", "s2", "worker", base, base + timedelta(minutes=5), 0.0,
            provider="openrouter")
    write_events(manager.events, "run_loc", [
        {"kind": "step", "step": 1, "output_tokens": 200, "generation_ms": 10_000},
    ])
    write_events(manager.events, "run_cld", [
        {"kind": "step", "step": 1, "output_tokens": 800, "generation_ms": 10_000},
    ])
    both = stats_for_runs(manager, ["run_loc", "run_cld"])
    speeds = (both["tok_per_s_local"], both["tok_per_s_cloud"], both["tok_per_s"])
    assert speeds == (20.0, 80.0, 50.0)
    cloud_only = stats_for_runs(manager, ["run_cld"])
    speeds = (cloud_only["tok_per_s_local"], cloud_only["tok_per_s_cloud"], cloud_only["tok_per_s"])
    assert speeds == (0.0, 80.0, 80.0)
