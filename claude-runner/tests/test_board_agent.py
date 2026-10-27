from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

OPS_DIR = Path(__file__).resolve().parents[1].parent / "data-tables" / "ops"
AGENT_PATH = OPS_DIR / "board_agent.py"


def _load_agent() -> ModuleType:
    spec = importlib.util.spec_from_file_location("board_agent_under_test", AGENT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["board_agent_under_test"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("board_agent_under_test", None)
        raise
    return module


class FakeDb:
    def __init__(
        self,
        x_result: int | None = None,
        q_result: list[Any] | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self.x_result = x_result
        self.q_result = q_result
        self.raise_error = raise_error
        self.x_calls: list[str] = []
        self.x_params: list[Sequence[object] | None] = []
        self.q_calls: list[str] = []

    def x(self, sql: str, params: Sequence[object] | None = None) -> int:
        self.x_calls.append(sql)
        self.x_params.append(params)
        if self.raise_error is not None:
            raise self.raise_error
        assert self.x_result is not None
        return self.x_result

    def q(self, sql: str, params: Sequence[object] | None = None) -> list[Any]:
        self.q_calls.append(sql)
        if self.raise_error is not None:
            raise self.raise_error
        assert self.q_result is not None
        return self.q_result


class SpawnGuard:
    """Stands in for subprocess.run: records every call and fails it unless a reply is set."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.reply: subprocess.CompletedProcess[str] | None = None
        self.error: Exception | None = None

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), kwargs))
        if self.error is not None:
            raise self.error
        if self.reply is None:
            raise AssertionError(f"unexpected subprocess: {argv}")
        return self.reply


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> SpawnGuard:
    guard = SpawnGuard()
    monkeypatch.setattr(subprocess, "run", guard)
    monkeypatch.delenv("BOARD_AGENT_DRY", raising=False)
    return guard


def _result_written(db: FakeDb) -> dict[str, Any]:
    params = db.x_params[-1]
    assert params is not None
    result: dict[str, Any] = json.loads(str(params[0]))
    return result


def test_claim_with_no_changed_rows_returns_none_without_readback() -> None:
    agent = _load_agent()
    db = FakeDb(x_result=0)
    client = agent.BoardClient(db)
    assert client.claim_next() is None
    assert db.q_calls == []


def test_claim_returns_exactly_one_row() -> None:
    agent = _load_agent()
    row = {"id": 7, "action": "resume", "args_json": {}, "confirm_token": None}
    db = FakeDb(x_result=1, q_result=[row])
    client = agent.BoardClient(db)
    claimed = client.claim_next()
    assert claimed is row
    assert len(db.q_result or []) == 1
    assert db.q_calls == [agent.CLAIMED_SQL]


def test_snapshot_strips_secret_keys() -> None:
    agent = _load_agent()
    db = FakeDb(x_result=1)
    client = agent.BoardClient(db)
    snapshot = {
        "queue": {"alive": True, "mission": "m1"},
        "tg_relay": {"alive": True, "last_update_id": 9},
        "docker_vm": {"avail_mb": 100},
        "token": "hunter2",
        "chat_id": 123,
        "environ": {"HOME": "/home/u"},
        "unknown_section": {"api_key": "abc"},
    }
    assert client.publish_snapshot(snapshot) is True
    params = db.x_params[0]
    assert params is not None
    kept = json.loads(str(params[0]))
    assert kept == {
        "queue": {"alive": True, "mission": "m1"},
        "tg_relay": {"alive": True, "last_update_id": 9},
        "docker_vm": {"avail_mb": 100},
    }


def test_write_result_rejects_running_status() -> None:
    agent = _load_agent()
    db = FakeDb(x_result=1)
    client = agent.BoardClient(db)
    with pytest.raises(ValueError):
        client.write_result({"id": 7}, "running", {"ok": True})
    assert db.x_calls == []


def test_db_timeout_is_logged_and_claim_returns_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = _load_agent()
    db = FakeDb(
        raise_error=subprocess.TimeoutExpired("docker exec ahawr-postgres psql", 30)
    )
    client = agent.BoardClient(db)
    with caplog.at_level(logging.ERROR, logger="board_agent"):
        assert client.claim_next() is None
    assert db.q_calls == []
    assert "timed out" in caplog.text


def test_reject_command_writes_reason_and_reports_one_row() -> None:
    agent = _load_agent()
    db = FakeDb(x_result=1)
    client = agent.BoardClient(db)
    assert client.reject_command({"id": 7}, "not implemented") is True
    params = db.x_params[0]
    assert params is not None
    assert json.loads(str(params[0])) == {"reason": "not implemented"}


def test_reject_command_reports_false_when_no_row_changed() -> None:
    agent = _load_agent()
    db = FakeDb(x_result=0)
    client = agent.BoardClient(db)
    assert client.reject_command({"id": 7}, "not implemented") is False


def test_unknown_action_is_rejected_with_reason_in_result_json(spawned: SpawnGuard) -> None:
    agent = _load_agent()
    db = FakeDb(x_result=1)
    client = agent.BoardClient(db)
    row = {"id": 8, "action": "drop_tables", "args_json": {}, "confirm_token": "tok"}
    assert agent._handle_command(client, row, 0.0) is False
    assert "'rejected'" in db.x_calls[0]
    assert _result_written(db) == {"reason": "unknown action"}
    assert spawned.calls == []


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("rebuild_service", {"service": "ahawr-postgres"}),
        ("rebuild_service", {"service": "claude-runner; docker rm -f ahawr-postgres"}),
        ("rebuild_service", {"service": ["claude-runner"]}),
        ("docker_update_memory", {"service": "claude-runner", "gb": "9"}),
        ("set_llama_preset", {"preset": "enable_thinking && rm"}),
    ],
)
def test_argument_outside_allowed_values_never_reaches_argv(
    spawned: SpawnGuard, monkeypatch: pytest.MonkeyPatch, action: str, args: dict[str, Any]
) -> None:
    agent = _load_agent()
    # no action with argument whitelists is implemented yet, so the value guard is
    # reached only when the action is treated as implemented
    monkeypatch.setattr(agent, "IMPLEMENTED_ACTIONS", agent.IMPLEMENTED_ACTIONS | {action})
    row = {"id": 9, "action": action, "args_json": args, "confirm_token": "tok"}
    plan = agent.validate_command(row)
    assert isinstance(plan, agent.Rejected)
    assert "outside the allowed values" in plan.reason
    assert spawned.calls == []


@pytest.mark.parametrize("action", ["stop_mission", "pause_at_boundary", "restart_queue"])
def test_dangerous_action_without_confirm_is_not_executed(
    spawned: SpawnGuard, action: str
) -> None:
    agent = _load_agent()
    db = FakeDb(x_result=1)
    client = agent.BoardClient(db)
    row = {"id": 10, "action": action, "args_json": {}, "confirm_token": None}
    assert agent._handle_command(client, row, 0.0) is False
    assert _result_written(db) == {"reason": f"{action} has no server confirm"}
    assert spawned.calls == []


def test_dangerous_action_with_confirm_reaches_the_runner(spawned: SpawnGuard) -> None:
    agent = _load_agent()
    spawned.reply = subprocess.CompletedProcess(["x"], 0, "", "")
    db = FakeDb(x_result=1)
    client = agent.BoardClient(db)
    row = {"id": 11, "action": "restart_queue", "args_json": {}, "confirm_token": "tok"}
    assert agent._handle_command(client, row, 0.0) is False
    assert spawned.calls and spawned.calls[0][0][0] == "powershell.exe"
    params = db.x_params[-1]
    assert params is not None
    assert params[0] == "done"
    assert json.loads(str(params[1])) == {"queue_chain": "restarted"}


def test_dry_run_prints_argv_and_spawns_nothing(
    spawned: SpawnGuard, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BOARD_AGENT_DRY", "1")
    agent = _load_agent()
    done = agent.run_argv(["docker", "stop", "x"])
    assert done.returncode == 0
    assert "DRY RUN, not executed: ['docker', 'stop', 'x']" in capsys.readouterr().out
    assert spawned.calls == []


def test_dry_run_resume_writes_done_without_spawning(
    spawned: SpawnGuard, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BOARD_AGENT_DRY", "1")
    agent = _load_agent()
    db = FakeDb(x_result=1)
    client = agent.BoardClient(db)
    row = {"id": 12, "action": "resume", "args_json": {}, "confirm_token": None}
    assert agent._handle_command(client, row, 0.0) is False
    assert "DRY RUN" in capsys.readouterr().out
    assert spawned.calls == []
    assert db.x_params[-1] is not None and db.x_params[-1][0] == "done"


def test_run_argv_cuts_output_to_max_output_chars_and_passes_timeout(
    spawned: SpawnGuard,
) -> None:
    agent = _load_agent()
    spawned.reply = subprocess.CompletedProcess(
        ["x"], 3, "o" * (agent.MAX_OUTPUT_CHARS + 50), "e" * (agent.MAX_OUTPUT_CHARS + 1)
    )
    done = agent.run_argv(["x"])
    assert done.returncode == 3
    assert len(done.stdout) == agent.MAX_OUTPUT_CHARS
    assert len(done.stderr) == agent.MAX_OUTPUT_CHARS
    kwargs = spawned.calls[0][1]
    assert kwargs["timeout"] == agent.SUBPROCESS_TIMEOUT_SECONDS
    assert kwargs["shell"] is False


def test_run_argv_timeout_returns_no_returncode(spawned: SpawnGuard) -> None:
    agent = _load_agent()
    spawned.error = subprocess.TimeoutExpired(["x"], agent.SUBPROCESS_TIMEOUT_SECONDS)
    done = agent.run_argv(["x"])
    assert done.returncode is None
    assert "timeout" in done.stderr


def _log_file(tmp_path: Path, lines: Sequence[str] = ()) -> Path:
    path = tmp_path / "real_queue.log"
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def test_resume_does_not_start_second_queue_watch(spawned: SpawnGuard) -> None:
    agent = _load_agent()
    spawned.reply = subprocess.CompletedProcess(["x"], 0, "1\n", "")
    db = FakeDb(x_result=1)
    client = agent.BoardClient(db)
    row = {"id": 20, "action": "resume", "args_json": {}, "confirm_token": None}
    assert agent._handle_command(client, row, 0.0) is False
    argvs = [argv for argv, _ in spawned.calls]
    assert argvs == [agent.PROCESS_LIST_ARGV["queue_watch_running"]]
    assert json.loads(str(db.x_params[-1][1])) == {
        "started": False,
        "reason": "queue_watch already running",
    }


def test_resume_starts_queue_watch_once_through_fixed_argv(spawned: SpawnGuard) -> None:
    agent = _load_agent()
    spawned.reply = subprocess.CompletedProcess(["x"], 0, "0\n", "")
    db = FakeDb(x_result=1)
    client = agent.BoardClient(db)
    row = {"id": 21, "action": "resume", "args_json": {}, "confirm_token": None}
    assert agent._handle_command(client, row, 0.0) is False
    assert len(spawned.calls) == 2
    assert spawned.calls[0][0] == agent.PROCESS_LIST_ARGV["queue_watch_running"]
    start_argv = spawned.calls[1][0]
    assert start_argv[:3] == ["powershell.exe", "-NoProfile", "-Command"]
    assert agent.QUEUE_WATCH_COMMAND_LINE in start_argv[-1]
    assert json.loads(str(db.x_params[-1][1])) == {"started": True}


def test_pause_at_boundary_waits_while_a_task_runs(
    spawned: SpawnGuard, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = _load_agent()
    queue_log = _log_file(tmp_path, ["ended t0: ok", "started t1 (run 1)"])
    monkeypatch.setattr(agent, "REAL_QUEUE_LOG", queue_log)
    spawned.reply = subprocess.CompletedProcess(["x"], 0, "1\n", "")
    outcome = agent.pause_at_boundary(0.0, 60.0)
    assert outcome.status == agent.WAITING
    assert [argv for argv, _ in spawned.calls] == [agent.PROCESS_LIST_ARGV["queue_alive"]]


def test_pause_command_stays_pending_while_a_task_runs(
    spawned: SpawnGuard, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = _load_agent()
    monkeypatch.setattr(agent, "REAL_QUEUE_LOG", _log_file(tmp_path, ["started t1 (run 1)"]))
    spawned.reply = subprocess.CompletedProcess(["x"], 0, "1\n", "")
    db = FakeDb(x_result=1)
    client = agent.BoardClient(db)
    row = {"id": 22, "action": "pause_at_boundary", "args_json": {}, "confirm_token": "tok"}
    assert agent._handle_command(client, row, agent.time.monotonic()) is True
    assert db.x_calls == []
    assert len(spawned.calls) == 1


def test_pause_at_boundary_stops_chain_once_task_ends(
    spawned: SpawnGuard, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = _load_agent()
    queue_log = _log_file(tmp_path, ["started t1 (run 1)", "ended t1: ok"])
    monkeypatch.setattr(agent, "REAL_QUEUE_LOG", queue_log)
    spawned.reply = subprocess.CompletedProcess(["x"], 0, "1\n", "")
    outcome = agent.pause_at_boundary(0.0, 60.0)
    assert outcome.status == agent.DONE
    assert outcome.result["queue_chain"] == "stopped"
    assert len(spawned.calls) == 2
    assert agent.KILL_PATTERNS["queue_chain"] in spawned.calls[1][0][-1]


def test_pause_at_boundary_expires_at_deadline_without_stopping(
    spawned: SpawnGuard, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = _load_agent()
    monkeypatch.setattr(agent, "REAL_QUEUE_LOG", _log_file(tmp_path, ["started t1 (run 1)"]))
    spawned.reply = subprocess.CompletedProcess(["x"], 0, "1\n", "")
    before = agent.pause_at_boundary(0.0, agent.PAUSE_DEADLINE_SECONDS - 1)
    assert before.status == agent.WAITING
    expired = agent.pause_at_boundary(0.0, agent.PAUSE_DEADLINE_SECONDS)
    assert expired.status == agent.FAILED
    assert "nothing stopped" in expired.result["reason"]
    assert len(spawned.calls) == 1


def test_stop_mission_cancels_running_worker_then_kills_chain(spawned: SpawnGuard) -> None:
    agent = _load_agent()
    run = {"run_id": "run-1", "role": "worker", "status": "running"}
    spawned.reply = subprocess.CompletedProcess(["x"], 0, json.dumps({"runs": [run]}), "")
    outcome = agent.stop_mission()
    assert outcome == agent.Outcome(
        agent.DONE, {"cancelled_run_id": "run-1", "queue_chain": "stopped"}
    )
    argvs = [argv for argv, _ in spawned.calls]
    assert argvs[0] == agent._runner_argv(f"/api/runs?limit={agent.RUNS_SCAN_LIMIT}")
    assert argvs[1] == agent._runner_argv("/api/runs/run-1/cancel", post=True)
    assert argvs[2][:3] == ["powershell.exe", "-NoProfile", "-Command"]
    assert agent.KILL_PATTERNS["queue_chain"] in argvs[2][-1]


def test_restart_queue_kills_chain_then_starts_it_with_fixed_argv(spawned: SpawnGuard) -> None:
    agent = _load_agent()
    spawned.reply = subprocess.CompletedProcess(["x"], 0, "", "")
    assert agent.restart_queue() == agent.Outcome(agent.DONE, {"queue_chain": "restarted"})
    assert len(spawned.calls) == 2
    assert agent.KILL_PATTERNS["queue_chain"] in spawned.calls[0][0][-1]
    assert agent.QUEUE_CHAIN_COMMAND_LINE in spawned.calls[1][0][-1]


def test_claim_lost_to_another_claim_executes_nothing(spawned: SpawnGuard, tmp_path: Path) -> None:
    agent = _load_agent()
    db = FakeDb(x_result=0)
    client = agent.BoardClient(db, log_path=_log_file(tmp_path))
    state = agent.LoopState()
    agent.run_tick(client, state, 0.0)
    assert state.pending is None
    assert db.q_calls == []
    assert spawned.calls == []
    assert not any("finished_at" in sql for sql in db.x_calls)


def test_run_tick_executes_at_most_max_commands(spawned: SpawnGuard, tmp_path: Path) -> None:
    agent = _load_agent()
    row = {"id": 30, "action": "drop_tables", "args_json": {}, "confirm_token": None}
    db = FakeDb(x_result=1, q_result=[row])
    client = agent.BoardClient(db, log_path=_log_file(tmp_path))
    agent.run_tick(client, agent.LoopState(), 0.0)
    assert len(db.q_calls) == agent.MAX_COMMANDS_PER_TICK == 1
    agent.run_tick(client, agent.LoopState(), 0.0, max_commands=3)
    assert len(db.q_calls) == 1 + 3
    assert spawned.calls == []


def test_run_tick_writes_heartbeat_each_tick_and_publishes_on_interval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = _load_agent()
    db = FakeDb(x_result=0)
    client = agent.BoardClient(db, log_path=_log_file(tmp_path))
    queue_publishes: list[int] = []
    snapshot_publishes: list[int] = []

    def publish_queue_log() -> bool:
        queue_publishes.append(1)
        return True

    def publish_snapshot(snapshot: Any) -> bool:
        snapshot_publishes.append(1)
        return True

    monkeypatch.setattr(client, "publish_queue_log", publish_queue_log)
    monkeypatch.setattr(client, "publish_snapshot", publish_snapshot)
    state = agent.LoopState()
    queue_counts: list[int] = []
    snapshot_counts: list[int] = []
    for now in (0.0, 5.0, 10.0, 15.0, 20.0):
        agent.run_tick(client, state, now, collect_snapshot=lambda: {})
        queue_counts.append(len(queue_publishes))
        snapshot_counts.append(len(snapshot_publishes))
    assert queue_counts == [1, 1, 2, 2, 3]
    assert snapshot_counts == [1, 1, 2, 2, 3]
    assert sum("board.agent" in sql for sql in db.x_calls) == 5


@pytest.mark.parametrize("action", ["restart_tg_relay", "send_test_notification"])
def test_action_not_implemented_in_agent_is_rejected(spawned: SpawnGuard, action: str) -> None:
    agent = _load_agent()
    db = FakeDb(x_result=1)
    client = agent.BoardClient(db)
    row = {"id": 40, "action": action, "args_json": {}, "confirm_token": "tok"}
    assert agent._handle_command(client, row, 0.0) is False
    assert "'rejected'" in db.x_calls[0]
    assert _result_written(db) == {"reason": f"{action} is not implemented in the agent"}
    assert spawned.calls == []


def test_main_exits_after_max_ticks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    agent = _load_agent()
    db = FakeDb(x_result=0)
    real_client = agent.BoardClient
    log_path = _log_file(tmp_path)
    monkeypatch.setattr(agent, "PgdbDb", lambda: db)
    monkeypatch.setattr(agent, "BoardClient", lambda database: real_client(database, log_path))
    monkeypatch.setattr(agent, "STOP_FILE", tmp_path / "board_agent.stop")
    assert agent.main(max_ticks=3, poll_seconds=0) == 0
    assert sum("board.agent" in sql for sql in db.x_calls) == 3
