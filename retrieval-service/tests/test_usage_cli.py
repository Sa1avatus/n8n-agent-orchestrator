"""Tests for the ``ahawr-retrieval usage`` CLI subcommand.

The runner API is mocked with respx (no network access); the retrieval log is a
temporary sqlite file built via :class:`RetrievalLog`. Invocation goes through
``cli.main`` inside a fresh event loop (``asyncio.run``); ``--help`` is checked
via a subprocess for the exact help text.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import respx

from ahawr_retrieval.cli import main
from ahawr_retrieval.logstore import RetrievalLog
from tests.test_usage import _cand, _log_row, _run

RUNNER_URL = "http://runner.example.test"


def _write_log(
    log: RetrievalLog, requests: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> None:
    for request in requests:
        log.write(
            request,
            [c for c in candidates if c["request_id"] == request["request_id"]],
        )


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    """A temporary sqlite retrieval log with one worker request and one reviewer request."""
    db = tmp_path / "logs.sqlite"
    log = RetrievalLog(db)
    requests = [
        _log_row("r1", 100.0, "worker", "m1", "T1", 100),
        _log_row("r2", 200.0, "reviewer", "m1", "T1", 50),
    ]
    candidates = [
        _cand("r1", "c1", "ws", "app/parser.py", 30, True),
        _cand("r1", "c2", "ws", "web/client.ts", 20, True),
        _cand("r2", "c3", "ws", "tests/test_parser.py", 15, True),
    ]
    _write_log(log, requests, candidates)
    log.close()
    return db


def _mock_runs(
    mock, runs: list[dict[str, Any]], events_by_run: dict[str, list[dict[str, Any]]]
) -> None:
    """Register the respx routes for the given runs/events on an open mock router."""
    mock.get(url__regex=rf"{re.escape(RUNNER_URL)}/v1/runs\?limit=\d+$").respond(
        json={"runs": runs}
    )
    for run_id, events in events_by_run.items():
        mock.get(
            url__regex=(
                rf"{re.escape(RUNNER_URL)}/v1/runs/"
                rf"{re.escape(run_id)}/events\?after=\d+$"
            )
        ).respond(
            json={
                "run": {"run_id": run_id},
                "events": events,
                "next": len(events),
                "live": None,
            }
        )


def _invoke(
    argv: list[str],
    runs: list[dict[str, Any]],
    events_by_run: dict[str, list[dict[str, Any]]],
    capsys,
) -> str:
    """Run ``main(argv)`` on a fresh event loop with a fresh respx mock, return stdout."""

    async def run() -> None:
        async with respx.mock(assert_all_called=False) as mock:
            _mock_runs(mock, runs, events_by_run)
            with contextlib.suppress(SystemExit):
                main(argv)

    capsys.readouterr()  # clear any prior output
    asyncio.run(run())
    return capsys.readouterr().out


def test_usage_prints_table_and_json(fixture_db: Path, capsys) -> None:
    runs = [_run("R1", 110.0, mission_id="m1", task_id="T1")]
    events = [
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/app/parser.py"}},
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/tests/conftest.py"}},
        {"kind": "tool_use", "name": "Bash", "input": {"command": "ahawr-search --corpus ws 'a'"}},
    ]

    # Compact table output.
    out = _invoke(
        ["usage", "--runner-url", RUNNER_URL, "--db", str(fixture_db)],
        runs,
        {"R1": events},
        capsys,
    )
    assert "profile" in out
    assert "worker" in out
    assert "reviewer" in out
    # worker: 2 selected, 2 opened (parser.py matched, conftest.py missed), 1 search.
    assert re.search(r"worker\s+2\s+2\s+0\.500\s+0\.500\s+0\.300\s+1", out)
    # reviewer: 1 selected, 0 opened.
    assert re.search(r"reviewer\s+1\s+0\s+0\.000\s+0\.000\s+0\.000\s+0", out)
    # Missed file block.
    assert "missed files (worker):" in out
    assert "conftest.py" in out

    # --json: full report as JSON with the expected keys.
    out_json = _invoke(
        ["usage", "--runner-url", RUNNER_URL, "--db", str(fixture_db), "--json"],
        runs,
        {"R1": events},
        capsys,
    )
    data = json.loads(out_json)
    assert set(data.keys()) == {"worker", "reviewer"}
    worker = data["worker"]
    assert worker["selected_files"] == 2
    assert worker["opened_files"] == 2
    assert worker["precision"] == pytest.approx(0.5)
    assert worker["recall"] == pytest.approx(0.5)
    assert worker["token_share"] == pytest.approx(0.3)
    assert worker["ahawr_search_calls"] == 1
    assert worker["missed_files"] == [{"path": "/ws/tests/conftest.py", "requests": 1}]
    reviewer = data["reviewer"]
    assert reviewer["selected_files"] == 1
    assert reviewer["opened_files"] == 0
    assert reviewer["ahawr_search_calls"] == 0


def test_usage_profile_filter(fixture_db: Path, capsys) -> None:
    runs = [_run("R1", 110.0, mission_id="m1", task_id="T1")]
    events = [
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/app/parser.py"}},
    ]
    out = _invoke(
        ["usage", "--runner-url", RUNNER_URL, "--db", str(fixture_db), "--profile", "worker"],
        runs,
        {"R1": events},
        capsys,
    )
    assert "worker" in out
    assert "reviewer" not in out
    assert re.search(r"worker\s+2\s+1\s+0\.500\s+1\.000\s+0\.300\s+0", out)


def test_usage_top_limits_missed_files(fixture_db: Path, capsys) -> None:
    # Two missed files for the worker request; --top 1 must show only one.
    runs = [_run("R1", 110.0, mission_id="m1", task_id="T1")]
    events = [
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/tests/conftest.py"}},
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/other/notes.md"}},
    ]
    out = _invoke(
        ["usage", "--runner-url", RUNNER_URL, "--db", str(fixture_db), "--top", "1"],
        runs,
        {"R1": events},
        capsys,
    )
    assert "missed files (worker):" in out
    # Both missed files have count 1; sorted by path: /ws/other/notes.md comes first.
    assert "notes.md" in out
    assert "… 1 more" in out
    assert "conftest.py" not in out


def test_usage_since_window(fixture_db: Path, capsys) -> None:
    # r1 has ts=100, r2 has ts=200; --since 150 keeps only r2 (reviewer).
    out = _invoke(
        ["usage", "--runner-url", RUNNER_URL, "--db", str(fixture_db), "--since", "150"],
        [],
        {},
        capsys,
    )
    assert "reviewer" in out
    assert re.search(r"reviewer\s+1\s+0\s+0\.000\s+0\.000\s+0\.000\s+0", out)
    # worker is out of the window.
    assert not re.search(r"^\s*worker\s", out, re.MULTILINE)


def test_usage_iso_since_window(fixture_db: Path, capsys) -> None:
    # ISO timestamp far in the past keeps everything; ISO far in the future keeps none.
    out_all = _invoke(
        [
            "usage",
            "--runner-url",
            RUNNER_URL,
            "--db",
            str(fixture_db),
            "--since",
            "1970-01-01T00:00:00Z",
        ],
        [],
        {},
        capsys,
    )
    assert "worker" in out_all and "reviewer" in out_all

    out_none = _invoke(
        [
            "usage",
            "--runner-url",
            RUNNER_URL,
            "--db",
            str(fixture_db),
            "--since",
            "2999-01-01T00:00:00Z",
        ],
        [],
        {},
        capsys,
    )
    assert "no usage data" in out_none


def test_usage_bash_operands_and_tools(fixture_db: Path, capsys) -> None:
    # Exercises the Bash-operand parser and Read/Edit/Write tools through usage_report:
    #   grep with -n flag and pattern, && chain of cat, redirect token, a non-read
    #   command (echo), Edit and Write tools, and an ahawr-search command.
    runs = [_run("R1", 110.0, mission_id="m1", task_id="T1")]
    events = [
        # grep -n "class X" app/parser.py -> app/parser.py (pattern skipped)
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": 'grep -n "class X" app/parser.py'},
        },
        # cat a.py && sed -n '1,50p' b.py -> a.py, b.py (sed script skipped)
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "cat a.py && sed -n '1,50p' b.py"},
        },
        # cat c.py >out.txt -> c.py (redirect target not counted)
        {"kind": "tool_use", "name": "Bash", "input": {"command": "cat c.py >out.txt"}},
        # echo not a read command -> nothing
        {"kind": "tool_use", "name": "Bash", "input": {"command": "echo hi"}},
        # Edit and Write tools contribute file_path
        {"kind": "tool_use", "name": "Edit", "input": {"file_path": "d.py"}},
        {"kind": "tool_use", "name": "Write", "input": {"file_path": "e.py"}},
        # ahawr-search counts as a search call
        {"kind": "tool_use", "name": "Bash", "input": {"command": "ahawr-search --corpus ws 'a'"}},
    ]
    out = _invoke(
        ["usage", "--runner-url", RUNNER_URL, "--db", str(fixture_db), "--json"],
        runs,
        {"R1": events},
        capsys,
    )
    data = json.loads(out)
    worker = data["worker"]
    # Selected files from the log: parser.py (30), client.ts (20). Opened from events:
    # parser.py, a.py, b.py, c.py, d.py, e.py. Intersection: parser.py only.
    # Distinct selected = 2 (parser.py, client.ts); distinct opened = 6.
    assert worker["selected_files"] == 2
    assert worker["opened_files"] == 6
    # precision = 1/2 = 0.5, recall = 1/6, searches = 1
    assert worker["precision"] == pytest.approx(0.5)
    assert worker["recall"] == pytest.approx(1 / 6)
    assert worker["ahawr_search_calls"] == 1
    # Missed: a.py, b.py, c.py, d.py, e.py (all opened but not selected).
    missed_paths = {m["path"] for m in worker["missed_files"]}
    assert missed_paths == {"a.py", "b.py", "c.py", "d.py", "e.py"}


def test_usage_trace_json_scope_fallback(tmp_path: Path, capsys) -> None:
    # A request stored without denormalized mission/task keys falls back to trace_json's
    # state_namespace/task_id, so the run still matches and is counted.
    db = tmp_path / "logs.sqlite"
    log = RetrievalLog(db)
    # Build a request row manually with empty trace_mission_id/trace_task_id but a
    # trace_json carrying state_namespace and task_id.
    raw = {
        "request_id": "r1",
        "ts": 100.0,
        "profile": "worker",
        "config_id": "c",
        "corpora": ["ws"],
        "query_hash": "q",
        "cache_status": "hit",
        "query_text": "q",
        "n_candidates": 1,
        "n_filtered": 0,
        "n_reranked": 1,
        "n_selected": 1,
        "context_tokens": 100,
        "trace": {"state_namespace": "m1", "task_id": "T1"},
    }
    log.write(raw, [_cand("r1", "c1", "ws", "app/parser.py", 30, True)])
    log.close()

    runs = [_run("R1", 110.0, mission_id="m1", task_id="T1")]
    events = [
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/app/parser.py"}},
    ]
    out = _invoke(
        ["usage", "--runner-url", RUNNER_URL, "--db", str(db)],
        runs,
        {"R1": events},
        capsys,
    )
    assert re.search(r"worker\s+1\s+1\s+1\.000\s+1\.000\s+0\.300\s+0", out)


def test_usage_relative_since(tmp_path: Path, capsys, monkeypatch) -> None:
    # ``--since -7d`` resolves to time.time() - 7d (a positive timestamp a week in the
    # past), so it keeps rows logged within the last 7 days and drops older ones. The
    # space form ``--since -7d`` must also parse (argparse glue is normalized).
    # time.time is pinned so the window is deterministic.
    now = 1_800_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)
    db = tmp_path / "logs.sqlite"
    log = RetrievalLog(db)
    requests = [
        # Within the last 7 days (kept by --since -7d).
        _log_row("r1", now - 3600.0, "worker", "m1", "T1", 100),
        # Older than 7 days (dropped by --since -7d).
        _log_row("r2", now - 8 * 86400.0, "reviewer", "m1", "T1", 50),
    ]
    _write_log(
        log,
        requests,
        [_cand("r1", "c1", "ws", "app/parser.py", 30, True)],
    )
    log.close()

    # -7d keeps only the recent worker row; the older reviewer row is dropped.
    out = _invoke(
        [
            "usage",
            "--runner-url",
            RUNNER_URL,
            "--db",
            str(db),
            "--since",
            "-7d",
        ],
        [],
        {},
        capsys,
    )
    assert "worker" in out
    assert not re.search(r"^\s*reviewer\s", out, re.MULTILINE)

    # A far-future absolute timestamp keeps nothing.
    out_none = _invoke(
        [
            "usage",
            "--runner-url",
            RUNNER_URL,
            "--db",
            str(db),
            "--since=9999999999",
        ],
        [],
        {},
        capsys,
    )
    assert "no usage data" in out_none


def test_usage_since_relative_subprocess_does_not_fail_argparse(tmp_path: Path, capsys) -> None:
    # Running through the real console script (subprocess), a relative ``--since`` in
    # space form must parse (argparse must not reject ``-7d``). With a missing db the
    # command exits 1 with "retrieval log not found", not exit 2 from argparse.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ahawr_retrieval.cli",
            "usage",
            "--runner-url",
            "http://127.0.0.1:9",
            "--db",
            str(tmp_path / "missing.sqlite"),
            "--since",
            "-7d",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 1
    assert "retrieval log not found" in proc.stderr


def test_usage_unreachable_runner_exits_nonzero(fixture_db: Path, capsys) -> None:
    # No mocked routes -> httpx transport error -> clear error message, exit 1.

    async def run() -> None:
        async with respx.mock(assert_all_called=False):
            with contextlib.suppress(SystemExit):
                main(["usage", "--runner-url", RUNNER_URL, "--db", str(fixture_db)])

    capsys.readouterr()
    asyncio.run(run())
    err = capsys.readouterr().err
    assert "not reachable" in err
    assert RUNNER_URL in err


def test_usage_missing_db_exits_nonzero(capsys) -> None:
    rc = main(
        [
            "usage",
            "--runner-url",
            RUNNER_URL,
            "--db",
            "/nonexistent/path/logs.sqlite",
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "retrieval log not found" in err


def test_usage_help_documents_all_options() -> None:
    help_text = subprocess.run(
        [sys.executable, "-m", "ahawr_retrieval.cli", "usage", "--help"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert help_text.returncode == 0
    text = help_text.stdout
    for option in (
        "--runner-url",
        "--since",
        "--db",
        "--profile",
        "--top",
        "--json",
    ):
        assert option in text, f"missing {option} in help"
    # Every option has a description in the help text.
    assert "claude-runner base URL" in text
    assert "unix timestamp or ISO/relative" in text
    assert "retrieval log sqlite path" in text
    assert "one profile only" in text
    assert "missed files" in text
    assert "full report as JSON" in text
