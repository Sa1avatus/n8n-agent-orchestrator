"""queue_watch starts real_queue_board.sh unless QUEUE_FROM_BOARD is 0. subprocess.run is faked."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

QUEUE_WATCH_PATH = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "queue_watch.py"
_spec = importlib.util.spec_from_file_location("queue_watch_under_test", QUEUE_WATCH_PATH)
assert _spec is not None and _spec.loader is not None
queue_watch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(queue_watch)

EXPECTED_FLAG_OFF_CMD = (
    "Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
    "-Arguments @{CommandLine='\"C:\\Program Files\\Git\\usr\\bin\\bash.exe\" "
    "-c \"export PATH=/usr/bin:/mingw64/bin:$PATH; cd /d/n8n/data-tables/ops && "
    "NOWAIT=1 /usr/bin/bash real_queue.sh >> real_queue.out 2>&1\"'} "
    "| Select-Object -ExpandProperty ReturnValue"
)


def set_flag(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("QUEUE_FROM_BOARD", raising=False)
    else:
        monkeypatch.setenv("QUEUE_FROM_BOARD", value)


def captured_start_command(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="0")

    monkeypatch.setattr(queue_watch.subprocess, "run", fake_run)
    queue_watch.start_queue()
    return calls[0][-1]


@pytest.mark.parametrize(
    ("value", "script"),
    [(None, "real_queue_board.sh"), ("1", "real_queue_board.sh"), ("0", "real_queue.sh")],
)
def test_queue_script_follows_flag(monkeypatch, value, script):
    set_flag(monkeypatch, value)
    assert queue_watch.queue_script() == script


def test_start_command_with_flag_off_is_unchanged(monkeypatch):
    set_flag(monkeypatch, "0")
    assert captured_start_command(monkeypatch) == EXPECTED_FLAG_OFF_CMD


@pytest.mark.parametrize("value", [None, "1"])
def test_start_command_with_flag_on_runs_board_script(monkeypatch, value):
    set_flag(monkeypatch, value)
    cmd = captured_start_command(monkeypatch)
    assert "NOWAIT=1 /usr/bin/bash real_queue_board.sh >> real_queue.out" in cmd
    assert cmd == EXPECTED_FLAG_OFF_CMD.replace("bash real_queue.sh ", "bash real_queue_board.sh ")


def test_alive_check_matches_both_scripts(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="1")

    monkeypatch.setattr(queue_watch.subprocess, "run", fake_run)
    assert queue_watch.queue_alive()
    cmd = calls[0][-1]
    assert "real_queue(_board)?\\.sh" in cmd
    assert "real_queue.sh" not in cmd
