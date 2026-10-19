"""Process-state aggregation and task-boundary checks for board ops."""

from __future__ import annotations

from claude_runner.board_ops_state import (
    STALE_AGENT_S,
    agent_status,
    at_task_boundary,
    check_boundary,
    interrupted_preview,
    process_panel,
)

NOW = "2026-10-17T12:00:00+00:00"


def _snapshot(queue=None, queue_watch=None, watchdog=None, relay=None, n8n=None):
    snapshot: dict = {"at": NOW}
    for key, value in (
        ("queue", queue),
        ("queue_watch", queue_watch),
        ("docker_watchdog", watchdog),
        ("tg_relay", relay),
        ("n8n", n8n),
    ):
        if value is not None:
            snapshot[key] = value
    return snapshot


def test_agent_status_alive_and_stale():
    assert agent_status("2026-10-17T11:59:59+00:00", NOW) == "alive"
    assert agent_status("2026-10-17T11:59:30+00:00", NOW) == "alive"  # not older than 30 s
    assert agent_status("2026-10-17T11:59:29+00:00", NOW) == "stale"  # older than 30 s
    assert agent_status(None, NOW) == "stale"
    assert agent_status("not-a-timestamp", NOW) == "stale"
    assert STALE_AGENT_S == 30.0


def test_process_panel_aggregates_queue_watchdog_relay_n8n_and_worker():
    runs = [
        {"run_id": "run-1", "role": "architect", "status": "running"},
        {
            "run_id": "run-2",
            "role": "worker",
            "status": "running",
            "title": "T012 — worker run",
            "run_id": "run-2",
            "kind": "run",
            "model": "local",
            "provider": "llama",
            "session_id": "sess-1",
            "claude_session_id": None,
            "cwd": "/d/n8n",
            "created_at": "2026-10-17T11:00:00+00:00",
            "started_at": "2026-10-17T11:00:01+00:00",
            "finished_at": None,
            "num_turns": 4,
            "cost_usd": 0.01,
            "context_tokens": 1000,
            "tokens": None,
            "updated_at": "2026-10-17T11:00:01+00:00",
            "source": "claude-code",
            "error": None,
            "live": True,
        },
    ]
    snapshot = _snapshot(
        queue={"alive": True, "mission": "llamacpp-speed-tuning",
               "task": "T012", "last_event_at": "2026-10-17T11:59:30+00:00"},
        queue_watch={"last_notice": "mission ended", "notices_per_hour": 2},
        watchdog={"last_action": "none", "restarts_today": 0},
        relay={"alive": True, "last_update_id": 108253},
        n8n={"running": True},
    )
    panel = process_panel(snapshot, runs)
    assert panel["queue"] == {
        "alive": True,
        "mission": "llamacpp-speed-tuning",
        "task": "T012",
        "last_event_age_s": 30.0,
    }
    assert panel["queue_watch"] == {"last_notice": "mission ended", "notices_per_hour": 2}
    assert panel["docker_watchdog"] == {"last_action": "none", "restarts_today": 0}
    assert panel["tg_relay"] == {"alive": True, "last_update_id": 108253}
    assert panel["n8n"] == {"running": True}
    assert panel["worker_run"] is not None
    assert panel["worker_run"]["run_id"] == "run-2"


def test_process_panel_empty_inputs():
    panel = process_panel(None, None)
    assert panel == {
        "queue": {"alive": None, "mission": None, "task": None, "last_event_age_s": None},
        "queue_watch": None,
        "docker_watchdog": None,
        "tg_relay": None,
        "n8n": None,
        "worker_run": None,
    }


def _queue_log(ended=None, started=None, started_newer=False):
    """Newest-first queue log rows of the ``ended`` / ``started`` lines.

    With both lines present, ``ended`` is newer by default;
    ``started_newer=True`` puts the ``started`` line first (newer)."""
    rows = []
    if started_newer:
        if started:
            rows.append({"at": "2026-10-17T11:30:00+00:00",
                         "line": f"started {started} (run 1, attempts per task: 3)"})
        if ended:
            rows.append({"at": "2026-10-17T11:00:00+00:00",
                         "line": f"ended {ended}: completed"})
    else:
        if ended:
            rows.append({"at": "2026-10-17T11:30:00+00:00",
                         "line": f"ended {ended}: completed"})
        if started:
            rows.append({"at": "2026-10-17T11:00:00+00:00",
                         "line": f"started {started} (run 1, attempts per task: 3)"})
    return rows


def test_at_task_boundary_no_mission():
    assert at_task_boundary({"queue": {"alive": False}}) is True
    assert at_task_boundary({}) is True


def test_at_task_boundary_mid_run_and_between_tasks():
    # Mid-run: the task started and no mission has ended yet.
    assert at_task_boundary(
        {"queue": {"alive": True},
         "queue_log": _queue_log(started="llamacpp-speed-tuning")}
    ) is False
    # The ended line is newer than the started line: the current task has
    # finished and the next one has not started yet.
    assert at_task_boundary(
        {"queue": {"alive": True},
         "queue_log": _queue_log(ended="llamacpp-speed-tuning",
                                 started="llamacpp-speed-tuning")}
    ) is True
    # A new task already started after the previous one ended: not a boundary.
    assert at_task_boundary(
        {"queue": {"alive": True},
         "queue_log": _queue_log(ended="llamacpp-speed-tuning",
                                 started="llamacpp-fa-longctx-decode",
                                 started_newer=True)}
    ) is False
    # The queue is alive but no marker line is published yet: conservatively
    # not a boundary.
    assert at_task_boundary(
        {"queue": {"alive": True}, "queue_log": []}
    ) is False


def test_check_boundary_rebuild_service():
    # Mid-run: refused.
    assert check_boundary(
        "rebuild_service",
        {"queue": {"alive": True},
         "queue_log": _queue_log(started="llamacpp-speed-tuning")},
    )
    # No mission running: allowed.
    assert check_boundary("rebuild_service", {"queue": {"alive": False}}) == []
    # At a task boundary (the ended line is newer than the started line):
    # allowed.
    assert check_boundary(
        "rebuild_service",
        {"queue": {"alive": True},
         "queue_log": _queue_log(ended="llamacpp-speed-tuning",
                                 started="llamacpp-speed-tuning")},
    ) == []
    # An action without a boundary requirement is never refused.
    assert check_boundary(
        "restart_queue",
        {"queue": {"alive": True},
         "queue_log": _queue_log(started="llamacpp-speed-tuning")},
    ) == []


def test_interrupted_preview():
    assert interrupted_preview({"queue": {"alive": False}}) is None
    assert interrupted_preview({}) is None
    assert interrupted_preview(
        {"queue": {"alive": True, "mission": "llamacpp-speed-tuning",
                  "task": "T012"}}
    ) == "mission llamacpp-speed-tuning — task T012"
    assert interrupted_preview(
        {"queue": {"alive": True, "mission": "llamacpp-speed-tuning"}}
    ) == "mission llamacpp-speed-tuning"
    assert interrupted_preview({"queue": {"alive": True}}) == "the queue"
