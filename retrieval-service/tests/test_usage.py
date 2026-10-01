"""Tests for the usage metrics module (usage.py).

Fixture data is a temporary sqlite log (built via RetrievalLog) and runner events
mocked through respx (no network access in tests).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
import respx

from ahawr_retrieval.logstore import RetrievalLog
from ahawr_retrieval.usage import (
    compute_usage,
    normalize_path,
    opened_files_from_events,
    selected_files,
    usage_report,
)

# ---------------------------------------------------------------------------
# Pure-function fixtures
# ---------------------------------------------------------------------------


def _log_row(
    request_id: str,
    ts: float,
    profile: str,
    trace_mission_id: str,
    trace_task_id: str,
    context_tokens: int,
    cache_status: str = "miss",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "ts": ts,
        "profile": profile,
        "trace_mission_id": trace_mission_id,
        "trace_task_id": trace_task_id,
        "task_mission_id": trace_mission_id,
        "task_task_id": trace_task_id,
        "config_id": "c1",
        "corpora": ["ws"],
        "corpora_json": '["ws"]',
        "query_component": "task",
        "component_value": "h",
        "trace_json": "{}",
        "query_text": "q",
        "query_hash": "qhash",
        "fingerprint_json": "{}",
        "cache_status": cache_status,
        "cache_source_request_id": None,
        "degraded": 0,
        "degraded_reasons_json": "[]",
        "n_candidates": 0,
        "n_filtered": 0,
        "n_reranked": 0,
        "n_selected": 0,
        "context_tokens": context_tokens,
        "timings_json": "{}",
        "snapshots_json": "{}",
        "embedder": "hashing",
        "reranker": "none",
    }


def _cand(
    request_id: str,
    chunk_id: str,
    corpus_id: str,
    path: str,
    token_count: int,
    selected: bool,
    final_rank: int = 1,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "chunk_id": chunk_id,
        "corpus_id": corpus_id,
        "path": path,
        "source_type": "code",
        "symbol": None,
        "section": None,
        "content_hash": "ch",
        "token_count": token_count,
        "chunk_age_seconds": 0.0,
        "lexical_score": None,
        "lexical_rank": None,
        "vector_score": None,
        "vector_rank": None,
        "symbol_score": None,
        "symbol_rank": None,
        "fused_score": None,
        "fused_rank": None,
        "reranker_score": None,
        "reranker_raw": None,
        "reranker_rank": None,
        "exact_symbol": None,
        "path_mentioned": None,
        "scope_match": None,
        "test_file": None,
        "source_prior": None,
        "deterministic_score": None,
        "final_score": None,
        "final_rank": final_rank,
        "freshness": "indexed",
        "filtered_reason": None,
        "selected": selected,
        "selection_reason": "budget",
    }


def _run(
    run_id: str, started_at: float, mission_id: str = "", task_id: str = "", role: str = "worker"
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "kind": "run",
        "title": "t",
        "status": "done",
        "role": role,
        "model": "m",
        "cwd": "/ws",
        "created_at": started_at - 1.0,
        "started_at": started_at,
        "finished_at": started_at + 10.0,
        "tokens": {"input": 10, "output": 5, "cache_read": 0, "cache_write": 0},
        "context_tokens": 100,
        "mission_id": mission_id,
        "task_id": task_id,
    }


def _events(*entries: dict[str, Any]) -> list[dict[str, Any]]:
    return list(entries)


# ---------------------------------------------------------------------------
# Pure-function tests (no I/O)
# ---------------------------------------------------------------------------


def test_normalize_path() -> None:
    assert normalize_path("/corpus/app/parser.py", "/corpus") == "app/parser.py"
    assert normalize_path("app/parser.py", "/corpus") == "app/parser.py"
    assert normalize_path("/other/file.py", "/corpus") == "other/file.py"
    assert normalize_path("file.py", "") == "file.py"
    assert normalize_path("", "/corpus") == ""


def test_opened_files_from_events() -> None:
    events = _events(
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/app/parser.py"}},
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "cat /ws/app/parser.py && grep foo /ws/web/client.ts"},
        },
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "ahawr-search --corpus ws 'invoice'"},
        },
        {
            "kind": "tool_use",
            "name": "Edit",
            "input": {"file_path": "/ws/tests/test_parser.py"},
        },
        {"kind": "text", "text": "thinking"},
        {"kind": "result", "text": "done"},
    )
    opened, searches = opened_files_from_events(events)
    assert opened == {"/ws/app/parser.py", "/ws/web/client.ts", "/ws/tests/test_parser.py"}
    assert searches == 1


def test_opened_files_bash_edge_cases() -> None:
    events = _events(
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "sed -i 's/a/b/' /ws/app/parser.py"},
        },
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "tail -n 5 /ws/logs/app.log"},
        },
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "echo hi > /ws/out.txt"},
        },
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "ahawr-search --help"},
        },
    )
    opened, searches = opened_files_from_events(events)
    # sed -i: the 's/a/b/' script is not a file; /ws/app/parser.py is.
    # tail -n 5: 5 is not a file; /ws/logs/app.log is.
    # echo is not a read command -> /ws/out.txt is NOT counted.
    # ahawr-search counts as a search call, not a file.
    assert opened == {"/ws/app/parser.py", "/ws/logs/app.log"}
    assert searches == 1


def test_opened_files_bash_grep_sed_cat() -> None:
    # Exact expected sets for grep -n (pattern not a file), sed -n '1,50p' (script not a
    # file), and cat pyproject.toml (pyproject.toml IS a file, not a sed expression).
    events = _events(
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": 'grep -n "class X" /ws/app/a.py'},
        },
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "sed -n '1,50p' /ws/app/b.py"},
        },
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "cat /ws/pyproject.toml"},
        },
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "grep -w foo /ws/app/c.py"},
        },
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "grep -e pat /ws/app/d.py"},
        },
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "sed -e 's/a/b/' /ws/app/e.py"},
        },
    )
    opened, searches = opened_files_from_events(events)
    # grep -n "class X": the pattern is a flag value, not a file; a.py is.
    # sed -n '1,50p': the address range is a flag value, not a file; b.py is.
    # cat pyproject.toml: it is a file (the old _is_sed_expression wrongly skipped it).
    # grep -w foo /ws/app/c.py: -w is a boolean flag (not in _VALUE_FLAGS);
    #   the pattern "foo" is the first operand, skipped; c.py is a file.
    # grep -e pat /ws/app/d.py: -e consumes its value (pattern); d.py is a file.
    # sed -e 's/a/b/' /ws/app/e.py: -e consumes its value (script); e.py is a file.
    assert opened == {
        "/ws/app/a.py",
        "/ws/app/b.py",
        "/ws/pyproject.toml",
        "/ws/app/c.py",
        "/ws/app/d.py",
        "/ws/app/e.py",
    }
    assert searches == 0


def test_selected_files() -> None:
    requests = [
        _log_row("r1", 100.0, "worker", "m1", "T1", 100),
        _log_row("r2", 200.0, "reviewer", "m1", "T1", 50),
    ]
    candidates = [
        _cand("r1", "c1", "ws", "app/parser.py", 30, True),
        _cand("r1", "c2", "ws", "web/client.ts", 20, False),
        _cand("r2", "c3", "ws", "app/parser.py", 10, True),
    ]
    result = selected_files(requests, candidates)
    assert result == {
        "r1": {"app/parser.py": 30},
        "r2": {"app/parser.py": 10},
    }


def test_compute_usage_single_profile() -> None:
    # One worker request, two selected files; a run opens one of them.
    requests = [_log_row("r1", 100.0, "worker", "m1", "T1", 100)]
    candidates = [
        _cand("r1", "c1", "ws", "app/parser.py", 30, True),
        _cand("r1", "c2", "ws", "web/client.ts", 20, True),
    ]
    runs = [_run("R1", 110.0, mission_id="m1", task_id="T1")]
    events = _events(
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/app/parser.py"}}
    )
    result = compute_usage(requests, candidates, runs, {"R1": events})
    worker = result["worker"]
    # selected = {parser.py, client.ts}; opened = {parser.py}
    assert worker["selected_files"] == 2
    assert worker["opened_files"] == 1
    assert worker["precision"] == pytest.approx(1 / 2)
    assert worker["recall"] == pytest.approx(1 / 1)
    # token_share = tokens of selected chunks whose file was opened (parser.py: 30) / context (100)
    assert worker["token_share"] == pytest.approx(30 / 100)
    assert worker["selected_tokens"] == 50
    assert worker["context_tokens"] == 100
    # missed = files opened but never selected: none
    assert worker["missed_files"] == []
    assert worker["ahawr_search_calls"] == 0


def test_compute_usage_missed_file() -> None:
    # A matched worker run opens a selected file (/ws/app/parser.py) and an
    # unselected file (/ws/tests/conftest.py). The unselected file must surface
    # in missed_files with requests == 1.
    requests = [_log_row("r1", 100.0, "worker", "m1", "T1", 100)]
    candidates = [
        _cand("r1", "c1", "ws", "app/parser.py", 30, True),
        _cand("r1", "c2", "ws", "web/client.ts", 20, True),
    ]
    runs = [_run("R1", 110.0, mission_id="m1", task_id="T1")]
    events = _events(
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/app/parser.py"}},
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/tests/conftest.py"}},
    )
    result = compute_usage(requests, candidates, runs, {"R1": events})
    worker = result["worker"]
    # selected = {parser.py, client.ts}; opened = {parser.py, /ws/tests/conftest.py}
    # precision = |{parser.py}| / |{parser.py, client.ts}| = 1/2
    # recall = |{parser.py}| / |{parser.py, /ws/tests/conftest.py}| = 1/2
    # token_share = tokens of selected&opened (parser.py: 30) / context (100)
    assert worker["selected_files"] == 2
    assert worker["opened_files"] == 2
    assert worker["precision"] == pytest.approx(0.5)
    assert worker["recall"] == pytest.approx(0.5)
    assert worker["token_share"] == pytest.approx(30 / 100)
    # missed = opened but not selected: /ws/tests/conftest.py (1 request)
    assert worker["missed_files"] == [{"path": "/ws/tests/conftest.py", "requests": 1}]
    assert worker["ahawr_search_calls"] == 0


def test_compute_usage_multiple_profiles_and_search() -> None:
    # Worker request r1 (2 selected files); reviewer request r2 (1 selected file);
    # a worker run opens r1's file and runs ahawr-search twice; a reviewer run opens r2's file.
    requests = [
        _log_row("r1", 100.0, "worker", "m1", "T1", 100),
        _log_row("r2", 200.0, "reviewer", "m1", "T1", 50),
    ]
    candidates = [
        _cand("r1", "c1", "ws", "app/parser.py", 30, True),
        _cand("r1", "c2", "ws", "web/client.ts", 20, True),
        _cand("r2", "c3", "ws", "tests/test_parser.py", 15, True),
    ]
    runs = [
        _run("R1", 110.0, mission_id="m1", task_id="T1"),
        _run("R2", 210.0, mission_id="m1", task_id="T1", role="reviewer"),
    ]
    worker_events = _events(
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/app/parser.py"}},
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "ahawr-search --corpus ws 'a'"},
        },
        {
            "kind": "tool_use",
            "name": "Bash",
            "input": {"command": "ahawr-search --corpus ws 'b'"},
        },
    )
    reviewer_events = _events(
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/tests/test_parser.py"}}
    )
    result = compute_usage(requests, candidates, runs, {"R1": worker_events, "R2": reviewer_events})
    worker = result["worker"]
    reviewer = result["reviewer"]
    # worker: selected {parser.py, client.ts}; opened {parser.py}; 2 ahawr-search calls
    assert worker["selected_files"] == 2
    assert worker["opened_files"] == 1
    assert worker["precision"] == pytest.approx(0.5)
    assert worker["recall"] == pytest.approx(1.0)
    # token_share = tokens of selected & opened (parser.py: 30) / context (100)
    assert worker["token_share"] == pytest.approx(30 / 100)
    assert worker["ahawr_search_calls"] == 2
    # missed = opened but not selected: none
    assert worker["missed_files"] == []
    # reviewer: selected {test_parser.py}; opened {test_parser.py}
    assert reviewer["selected_files"] == 1
    assert reviewer["opened_files"] == 1
    assert reviewer["precision"] == pytest.approx(1.0)
    assert reviewer["recall"] == pytest.approx(1.0)
    # token_share = tokens of selected & opened (test_parser.py: 15) / context (50)
    assert reviewer["token_share"] == pytest.approx(15 / 50)
    assert reviewer["ahawr_search_calls"] == 0
    assert reviewer["missed_files"] == []


def test_compute_usage_no_selection_or_opening() -> None:
    requests = [_log_row("r1", 100.0, "worker", "m1", "T1", 100)]
    candidates = [_cand("r1", "c1", "ws", "app/parser.py", 10, False)]
    runs = [_run("R1", 110.0, mission_id="m1", task_id="T1")]
    events = _events({"kind": "text", "text": "no tools"})
    result = compute_usage(requests, candidates, runs, {"R1": events})
    worker = result["worker"]
    assert worker["selected_files"] == 0
    assert worker["opened_files"] == 0
    assert worker["precision"] == 0.0
    assert worker["recall"] == 0.0
    assert worker["token_share"] == 0.0
    assert worker["missed_files"] == []
    assert worker["ahawr_search_calls"] == 0


def test_compute_usage_cross_task_and_unmatched_runs() -> None:
    # A file opened by another task's run must not count for this task; an unmatched
    # run (no correlation keys, or a different role) is dropped entirely.
    requests = [
        _log_row("r1", 100.0, "worker", "m1", "T1", 100),
        _log_row("r2", 200.0, "reviewer", "m1", "T1", 50),
        _log_row("r3", 150.0, "worker", "m1", "T2", 80),
    ]
    candidates = [
        _cand("r1", "c1", "ws", "app/parser.py", 30, True),
        _cand("r1", "c2", "ws", "web/client.ts", 20, True),
        _cand("r2", "c3", "ws", "tests/test_parser.py", 15, True),
        _cand("r3", "c4", "ws", "other/file.py", 10, True),
    ]
    runs = [
        # Matches r1 (worker, m1/T1, window [100, 200)).
        _run("R1", 110.0, mission_id="m1", task_id="T1"),
        # A DIFFERENT task (T2) — must NOT count toward r1 even though same role+mission.
        _run("R2", 120.0, mission_id="m1", task_id="T2"),
        # Unmatched: no correlation keys -> dropped.
        _run("R3", 130.0, mission_id="", task_id=""),
        # Unmatched: role "reviewer" != r1's profile "worker" -> dropped.
        _run("R4", 140.0, mission_id="m1", task_id="T1", role="reviewer"),
    ]
    events_r1 = _events(
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/app/parser.py"}},
    )
    events_r2 = _events(
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/other/file.py"}},
    )
    events_r3 = _events(
        {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/web/client.ts"}},
    )
    result = compute_usage(
        requests,
        candidates,
        runs,
        {
            "R1": events_r1,
            "R2": events_r2,
            "R3": events_r3,
        },
    )
    worker = result["worker"]
    reviewer = result["reviewer"]
    # r1 (worker, m1/T1): selected {parser.py, client.ts}; opened {parser.py} (only R1).
    # R2 opened /ws/other/file.py for task T2 -> NOT counted here.
    # R3 (no keys) and R4 (wrong role) are unmatched -> dropped.
    assert worker["selected_files"] == 3  # r1's 2 + r3's 1
    assert worker["opened_files"] == 1
    assert worker["precision"] == pytest.approx(1 / 3)
    assert worker["recall"] == pytest.approx(1 / 1)
    # token_share = selected&opened tokens (parser.py: 30) / context (100+80)
    assert worker["token_share"] == pytest.approx(30 / 180)
    # missed = opened but not selected for r1: none (parser.py was selected).
    assert worker["missed_files"] == []
    # reviewer (r2): selected {test_parser.py}; no matched run (R4 is role=reviewer but
    # started at 140, which is < r2.ts=200, so it does NOT match r2) -> opened empty.
    assert reviewer["selected_files"] == 1
    assert reviewer["opened_files"] == 0
    assert reviewer["precision"] == 0.0
    assert reviewer["recall"] == 0.0
    assert reviewer["token_share"] == 0.0
    assert reviewer["missed_files"] == []


def test_compute_usage_empty_inputs() -> None:
    result = compute_usage([], [], [], {})
    assert result == {}


# ---------------------------------------------------------------------------
# End-to-end tests with a temporary sqlite log and respx-mocked runner API
# ---------------------------------------------------------------------------

RUNNER_URL = "http://runner.example.test"


def _write_log(
    log: RetrievalLog, requests: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> None:
    for request in requests:
        log.write(request, [c for c in candidates if c["request_id"] == request["request_id"]])


@pytest.fixture
def tmp_log(tmp_path: Path) -> RetrievalLog:
    log = RetrievalLog(tmp_path / "logs.sqlite")
    yield log
    log.close()


def test_usage_report_e2e(tmp_log: RetrievalLog) -> None:
    requests = [
        _log_row("r1", 100.0, "worker", "m1", "T1", 100),
        _log_row("r2", 200.0, "reviewer", "m1", "T1", 50),
    ]
    candidates = [
        _cand("r1", "c1", "ws", "app/parser.py", 30, True),
        _cand("r1", "c2", "ws", "web/client.ts", 20, True),
        _cand("r2", "c3", "ws", "tests/test_parser.py", 15, True),
    ]
    _write_log(tmp_log, requests, candidates)

    runs = [
        _run("R1", 110.0, mission_id="m1", task_id="T1"),
        _run("R2", 210.0, mission_id="m1", task_id="T1", role="reviewer"),
    ]
    run_events = {
        "R1": _events(
            {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/app/parser.py"}},
            {
                "kind": "tool_use",
                "name": "Bash",
                "input": {"command": "ahawr-search --corpus ws 'a'"},
            },
            {
                "kind": "tool_use",
                "name": "Bash",
                "input": {"command": "ahawr-search --corpus ws 'b'"},
            },
        ),
        "R2": _events(
            {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/tests/test_parser.py"}}
        ),
    }

    async def run():
        async with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=rf"{re.escape(RUNNER_URL)}/v1/runs\?limit=\d+$").respond(
                json={"runs": runs}
            )
            for run_id, events in run_events.items():
                mock.get(
                    url__regex=rf"{re.escape(RUNNER_URL)}/v1/runs/{re.escape(run_id)}/events\?after=\d+$"
                ).respond(
                    json={
                        "run": {"run_id": run_id},
                        "events": events,
                        "next": len(events),
                        "live": None,
                    }
                )
            return await usage_report(tmp_log, RUNNER_URL)

    report = asyncio.run(run())
    worker = report["worker"]
    reviewer = report["reviewer"]
    # worker: selected {parser.py, client.ts}; opened {parser.py}; token_share = 30/100
    assert worker["selected_files"] == 2
    assert worker["opened_files"] == 1
    assert worker["precision"] == pytest.approx(0.5)
    assert worker["recall"] == pytest.approx(1.0)
    assert worker["token_share"] == pytest.approx(0.3)
    assert worker["ahawr_search_calls"] == 2
    # missed = opened but not selected: none
    assert worker["missed_files"] == []
    # reviewer: selected {test_parser.py}; opened {test_parser.py}; token_share = 15/50
    assert reviewer["selected_files"] == 1
    assert reviewer["opened_files"] == 1
    assert reviewer["precision"] == pytest.approx(1.0)
    assert reviewer["recall"] == pytest.approx(1.0)
    assert reviewer["token_share"] == pytest.approx(0.3)
    assert reviewer["ahawr_search_calls"] == 0


def test_usage_report_filters_profile(tmp_log: RetrievalLog) -> None:
    requests = [
        _log_row("r1", 100.0, "worker", "m1", "T1", 100),
        _log_row("r2", 200.0, "reviewer", "m1", "T1", 50),
    ]
    candidates = [
        _cand("r1", "c1", "ws", "app/parser.py", 30, True),
        _cand("r2", "c2", "ws", "web/client.ts", 10, True),
    ]
    _write_log(tmp_log, requests, candidates)
    runs = [_run("R1", 110.0, mission_id="m1", task_id="T1")]
    run_events = {
        "R1": _events(
            {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/app/parser.py"}}
        )
    }

    async def run():
        async with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=rf"{re.escape(RUNNER_URL)}/v1/runs\?limit=\d+$").respond(
                json={"runs": runs}
            )
            mock.get(url__regex=rf"{re.escape(RUNNER_URL)}/v1/runs/R1/events\?after=\d+$").respond(
                json={
                    "run": {"run_id": "R1"},
                    "events": run_events["R1"],
                    "next": 1,
                    "live": None,
                }
            )
            return await usage_report(tmp_log, RUNNER_URL, profile="worker")

    report = asyncio.run(run())
    assert set(report.keys()) == {"worker"}
    assert report["worker"]["selected_files"] == 1
    assert report["worker"]["precision"] == pytest.approx(1.0)


def test_usage_report_empty_runner(tmp_log: RetrievalLog) -> None:
    requests = [_log_row("r1", 100.0, "worker", "m1", "T1", 100)]
    candidates = [_cand("r1", "c1", "ws", "app/parser.py", 30, True)]
    _write_log(tmp_log, requests, candidates)

    async def run():
        async with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=rf"{re.escape(RUNNER_URL)}/v1/runs\?limit=\d+$").respond(
                json={"runs": []}
            )
            return await usage_report(tmp_log, RUNNER_URL)

    report = asyncio.run(run())
    # No runs -> nothing opened -> token_share = tokens of selected&opened / context = 0/100.
    assert report["worker"]["selected_files"] == 1
    assert report["worker"]["opened_files"] == 0
    assert report["worker"]["precision"] == 0.0
    assert report["worker"]["token_share"] == pytest.approx(0.0)


def test_usage_report_since_window(tmp_log: RetrievalLog) -> None:
    requests = [
        _log_row("r1", 100.0, "worker", "m1", "T1", 100),
        _log_row("r2", 500.0, "worker", "m1", "T1", 100),
    ]
    candidates = [
        _cand("r1", "c1", "ws", "app/parser.py", 30, True),
        _cand("r2", "c2", "ws", "web/client.ts", 20, True),
    ]
    _write_log(tmp_log, requests, candidates)
    runs = [_run("R1", 600.0, mission_id="m1", task_id="T1")]
    run_events = {
        "R1": _events(
            {"kind": "tool_use", "name": "Read", "input": {"file_path": "/ws/web/client.ts"}}
        )
    }

    async def run():
        async with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=rf"{re.escape(RUNNER_URL)}/v1/runs\?limit=\d+$").respond(
                json={"runs": runs}
            )
            mock.get(url__regex=rf"{re.escape(RUNNER_URL)}/v1/runs/R1/events\?after=\d+$").respond(
                json={
                    "run": {"run_id": "R1"},
                    "events": run_events["R1"],
                    "next": 1,
                    "live": None,
                }
            )
            return await usage_report(tmp_log, RUNNER_URL, since=400.0)

    report = asyncio.run(run())
    # Only r2 is in the window (ts=500 >= 400).
    assert report["worker"]["selected_files"] == 1
    assert report["worker"]["selected_tokens"] == 20
    assert report["worker"]["precision"] == pytest.approx(1.0)
