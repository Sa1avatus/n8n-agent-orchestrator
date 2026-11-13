from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from ahawr_runner.api import create_app
from ahawr_runner.board import InMemoryBoardStore
from ahawr_runner.config import Settings
from ahawr_runner.runs import RunManager
from ahawr_runner.store import Store
from tests.test_board_stats import PRICING_ENV, _add_run, _event, _seed_board, _write_run_events


def _manager(settings: Settings) -> RunManager:
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
    return RunManager(Settings.from_env(env), Store(settings.data_dir / "detail_runs.sqlite"))


def _seed(settings: Settings) -> tuple[InMemoryBoardStore, RunManager]:
    board = InMemoryBoardStore()
    _seed_board(board)
    manager = _manager(settings)
    base = datetime(2026, 1, 1, 10, 0)
    _add_run(manager.store, "run_w1", "s1", "worker", base,
             base + timedelta(minutes=10), 2.0, provider="local")
    _add_run(manager.store, "run_w2", "s1", "worker",
            base + timedelta(minutes=20), base + timedelta(minutes=25), 1.0, provider="local")
    _add_run(manager.store, "run_r1", "s2", "reviewer",
            base + timedelta(minutes=30), base + timedelta(minutes=35), 0.5)
    _write_run_events(manager.events, "run_w1", [
        {"kind": "step", "step": 1, "started": 100, "input_tokens": 100_000,
         "output_tokens": 10_000, "cache_read_tokens": 0,
         "cache_write_tokens": 0, "generation_ms": 1000},
    ])
    _write_run_events(manager.events, "run_w2", [
        {"kind": "step", "step": 1, "started": 100, "input_tokens": 100_000,
         "output_tokens": 10_000, "cache_read_tokens": 20_000,
         "cache_write_tokens": 0, "generation_ms": 1000},
    ])
    board.append_event("sk1", _event(
        "sk1", task_id="T001", task_index=0, task_attempt=0,
        event_type="worker_completed", worker_run_id="run_w1", worker_status="completed",
    ))
    board.append_event("sk1", _event(
        "sk1", task_id="T001", task_index=0, task_attempt=0,
        event_type="review_parsed", worker_run_id="run_w1",
        review_status="needs_changes", review_score=55.0, review_reason="not enough",
        reviewer_run_id="run_r1", reviewer_status="completed",
    ))
    board.append_event("sk1", _event(
        "sk1", task_id="T001", task_index=0, task_attempt=1,
        event_type="worker_completed", worker_run_id="run_w2", worker_status="completed",
    ))
    board.append_event("sk1", _event(
        "sk1", task_id="T001", task_index=0, task_attempt=1,
        event_type="review_parsed", worker_run_id="run_w2",
        review_status="pass", review_score=90.0, review_reason="ok",
        reviewer_run_id="run_r1", reviewer_status="completed",
    ))
    return board, manager


def test_dump_bytes(settings: Settings) -> None:
    board, manager = _seed(settings)
    app = create_app(settings)
    app.state.board_store = board
    with TestClient(app) as client:
        # the lifespan installs its own RunManager; swap it for the seeded one
        app.state.manager = manager
        print("MISSION:", repr(client.get("/api/board/missions/m1/stats").content))
        print("TASK:", repr(client.get("/api/board/missions/m1/tasks/T001/stats").content))
        print("PROJECT_BAD:", client.get("/api/board/projects/bad key/stats").status_code)
