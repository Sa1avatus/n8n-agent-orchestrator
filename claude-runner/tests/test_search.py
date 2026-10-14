"""Tests for the search module (search.py) and the GET /v1/search endpoint.

Journals are generated in temporary directories (never the live data directory).
"""

from __future__ import annotations

import json
import stat
import sys
import threading
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from claude_runner import api as api_module
from claude_runner import search as search_module
from claude_runner.api import create_app
from claude_runner.config import Settings

API_KEY = "test-bearer-token"

FAKE = Path(__file__).with_name("fake_claude.py")


@pytest.fixture
def fake_bin(tmp_path: Path) -> str:
    wrapper = tmp_path / "claude"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return str(wrapper)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "sub").mkdir(parents=True)
    return ws


@pytest.fixture(autouse=True)
def claude_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "calls.jsonl"))
    return config


@pytest.fixture
def settings(tmp_path: Path, fake_bin: str, workspace: Path) -> Settings:
    env = {
        "CLAUDE_RUNNER_DATA_DIR": str(tmp_path / "data"),
        "CLAUDE_RUNNER_CLAUDE_BIN": fake_bin,
        "CLAUDE_RUNNER_WORKSPACE": str(workspace),
        "CLAUDE_RUNNER_PATH_MAP": f"D:\\n8n\\workspace={workspace}",
        "CLAUDE_RUNNER_MAX_CONCURRENT": "2",
        "CLAUDE_RUNNER_API_KEY": API_KEY,
    }
    s = Settings.from_env(env)
    object.__setattr__(s, "interrupt_grace_seconds", 2.0)
    return s


@pytest.fixture
def events_dir(settings: Settings) -> Path:
    d = settings.data_dir / "events"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_journal(events_dir: Path, run_id: str, texts: list[str]) -> None:
    """Write a minimal event log for a run: one prompt, then assistant texts."""
    lines = [{"kind": "user", "text": f"prompt for {run_id}", "seq": 1}]
    for i, text in enumerate(texts, start=2):
        lines.append({"kind": "text", "text": text, "seq": i})
    path = events_dir / f"{run_id}.jsonl"
    path.write_text("\n".join(json.dumps(entry) for entry in lines) + "\n")


def store_rows(events_dir: Path, runs: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Build a list of run dicts in the same shape the Store returns.

    ``runs`` is given newest-first (the store's list order).
    """
    base = {
        "run_id": "",
        "session_id": "sess-1",
        "role": "worker",
        "model": "model-a",
        "provider": "local",
        "cwd": "/workspace",
        "status": "completed",
        "started_at": None,
    }
    rows = []
    for run_id, overrides in runs:
        row = dict(base)
        row.update(overrides)
        row["run_id"] = run_id
        rows.append(row)
    return rows


class FakeStore:
    """A stand-in for the Store: newest-first run ids and full run dicts."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def list_runs(
        self, limit: int = 50, session_id: str = "", role: str = "", status: str = ""
    ) -> list[dict[str, Any]]:
        return self._rows[:limit]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        for row in self._rows:
            if row["run_id"] == run_id:
                return row
        return None


def run_search(
    events_dir: Path,
    query: str,
    store: FakeStore | None = None,
    limit: int = 50,
    filters: dict[str, str] | None = None,
) -> dict[str, Any]:
    return search_module.search(events_dir, query, filters, store=store, limit=limit)


# ---------------------------------------------------------------------------
# search module: filters and per-run cap
# ---------------------------------------------------------------------------


def test_per_run_cap_and_newest_first(events_dir: Path) -> None:
    # run_new has 8 occurrences of "needle" in one entry; run_old has 3.
    write_journal(events_dir, "run_new", ["needle " * 8])
    write_journal(events_dir, "run_old", ["needle " * 3])
    rows = store_rows(events_dir, [("run_new", {}), ("run_old", {})])
    store = FakeStore(rows)
    result = run_search(events_dir, "needle", store=store)
    assert result["runs"][0]["run_id"] == "run_new"
    # capped at PER_RUN_MATCHES (5), newest first
    assert result["runs"][0]["matches"] == search_module.PER_RUN_MATCHES
    assert result["runs"][1]["run_id"] == "run_old"
    assert result["runs"][1]["matches"] == 3
    assert result["incomplete"] is False


def test_filter_by_role_and_provider(events_dir: Path) -> None:
    write_journal(events_dir, "run_a", ["needle here"])
    write_journal(events_dir, "run_b", ["needle here"])
    rows = store_rows(
        events_dir,
        [
            ("run_a", {"role": "worker", "provider": "local"}),
            ("run_b", {"role": "reviewer", "provider": "cloud"}),
        ],
    )
    store = FakeStore(rows)
    # role=worker keeps only run_a
    only_a = run_search(events_dir, "needle", store=store, filters={"role": "worker"})
    assert [r["run_id"] for r in only_a["runs"]] == ["run_a"]
    # role=worker AND provider=cloud matches none
    both = run_search(
        events_dir, "needle", store=store, filters={"role": "worker", "provider": "cloud"}
    )
    assert both["runs"] == []


def test_case_insensitivity(events_dir: Path) -> None:
    write_journal(events_dir, "run_x", ["NEEDLE and Needle"])
    rows = store_rows(events_dir, [("run_x", {})])
    store = FakeStore(rows)
    result = run_search(events_dir, "needle", store=store)
    assert result["runs"] and result["runs"][0]["matches"] == 2


def test_empty_and_single_char_queries(events_dir: Path) -> None:
    write_journal(events_dir, "run_x", ["needle here"])
    rows = store_rows(events_dir, [("run_x", {})])
    store = FakeStore(rows)
    assert run_search(events_dir, "", store=store) == {"runs": [], "incomplete": False}
    assert run_search(events_dir, "n", store=store) == {"runs": [], "incomplete": False}


def test_literal_special_characters_and_metacharacters(events_dir: Path) -> None:
    # quotes, angle brackets and a regex metacharacter are matched literally.
    write_journal(events_dir, "run_q", ['say "hello <world>" ok'])
    write_journal(events_dir, "run_m", ["a]b (c) {d} e.f * g"])
    rows = store_rows(events_dir, [("run_q", {}), ("run_m", {})])
    store = FakeStore(rows)
    # literal: the exact text appears in the returned data
    result = run_search(events_dir, "hello <world>", store=store)
    assert result["runs"][0]["run_id"] == "run_q"
    assert any('"hello <world>"' in s["snippet"] for s in result["runs"][0]["snippets"])
    # regex metacharacters are not interpreted
    result = run_search(events_dir, "a]b", store=store)
    assert result["runs"][0]["run_id"] == "run_m"
    # an unmatchable regex pattern matches nothing (treated literally)
    result = run_search(events_dir, "a(b", store=store)
    assert result["runs"] == []


# ---------------------------------------------------------------------------
# search module: total limit and the incomplete flag
# ---------------------------------------------------------------------------


def test_total_limit_and_incomplete_flag(events_dir: Path) -> None:
    # 5 runs, each with 3 matches. limit=3 returns exactly 3 runs (the max allowed)
    # and sets incomplete because more matching runs were skipped.
    for i in range(5):
        write_journal(events_dir, f"run{i}", ["aa bb", "cc dd", "ee ff"])
    rows = store_rows(events_dir, [(f"run{i}", {}) for i in range(5)])
    store = FakeStore(rows)
    result = run_search(events_dir, "aa", store=store, limit=3)
    assert len(result["runs"]) == 3  # the limit caps the number of runs returned
    assert sum(r["matches"] for r in result["runs"]) == 3  # "aa" matches once per run
    assert result["incomplete"] is True  # the result reached the limit
    # a limit larger than the number of matching runs does not set the flag
    result = run_search(events_dir, "aa", store=store, limit=10)
    assert len(result["runs"]) == 5
    assert sum(r["matches"] for r in result["runs"]) == 5
    assert result["incomplete"] is False


# ---------------------------------------------------------------------------
# search module: time budget via a monkeypatched clock
# ---------------------------------------------------------------------------


def test_time_budget_stops_scan_and_sets_incomplete(
    events_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A monkeypatched clock that advances on every call, with a modest budget: the deadline is
    # hit part-way through the run list, so the scan stops early and sets incomplete.
    for i in range(120):
        write_journal(events_dir, f"run{i:03d}", ["needle " * 5])
    rows = store_rows(events_dir, [(f"run{i:03d}", {}) for i in range(120)])
    store = FakeStore(rows)
    t = [0.0]

    def advancing() -> float:
        t[0] += 1.0
        return t[0]

    with mock.patch.object(search_module, "time") as fake_time:
        fake_time.monotonic = advancing
        # shrink the budget so the advancing clock blows past it part-way through
        original_budget = search_module.TIME_BUDGET_S
        search_module.TIME_BUDGET_S = 5.0
        try:
            result = run_search(events_dir, "needle", store=store, limit=200)
        finally:
            search_module.TIME_BUDGET_S = original_budget
    # the deadline was exceeded, so the scan stopped early and incomplete is set
    assert result["incomplete"] is True
    # some runs were returned but far fewer than all 120
    assert 0 < len(result["runs"]) < 120
    # the newest run is scanned first
    assert result["runs"][0]["run_id"] == "run000"


# ---------------------------------------------------------------------------
# endpoint: auth, bearer, query params, limit clamp, to_thread
# ---------------------------------------------------------------------------


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def test_search_requires_bearer(events_dir: Path, settings: Settings) -> None:
    with _client(settings) as client:
        # no bearer at all
        assert client.get("/v1/search", params={"q": "needle"}).status_code in (401, 403)
        # wrong bearer
        bad = client.get(
            "/v1/search", params={"q": "needle"}, headers={"Authorization": "Bearer wrong"}
        )
        assert bad.status_code in (401, 403)


def test_search_returns_matches_with_bearer(events_dir: Path, settings: Settings) -> None:
    write_journal(events_dir, "run_a", ["needle here"])
    write_journal(events_dir, "run_b", ["needle there"])
    rows = store_rows(events_dir, [("run_a", {}), ("run_b", {})])
    client = _client(settings)
    client.__enter__()
    # the endpoint searches runs.events.directory and uses the manager's store for run ids;
    # point the store at our generated runs so the response carries them
    with mock.patch.object(client.app.state.manager, "store", FakeStore(rows)):
        res = client.get(
            "/v1/search", params={"q": "needle"}, headers={"Authorization": f"Bearer {API_KEY}"}
        )
        assert res.status_code == 200
        body = res.json()
        ids = [r["run_id"] for r in body["runs"]]
        assert set(ids) == {"run_a", "run_b"}
        assert body["incomplete"] is False
    client.__exit__(None, None, None)


def test_search_clamps_limit_to_max(events_dir: Path, settings: Settings) -> None:
    seen = {}
    client = _client(settings)
    client.__enter__()
    original = search_module.search

    def capturing(events_dir_arg, query, filters=None, *, store=None, limit=50):
        seen["limit"] = limit
        return original(events_dir_arg, query, filters, store=store, limit=limit)

    with mock.patch.object(api_module, "run_search", capturing):
        res = client.get(
            "/v1/search",
            params={"q": "needle", "limit": 999999},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert res.status_code == 200
        # the endpoint clamps to MAX_LIMIT before calling search
        assert seen["limit"] == search_module.MAX_LIMIT
    client.__exit__(None, None, None)


def test_search_passes_filters_to_search_module(events_dir: Path, settings: Settings) -> None:
    seen = {}
    client = _client(settings)
    client.__enter__()
    original = search_module.search

    def capturing(events_dir_arg, query, filters=None, *, store=None, limit=50):
        seen["filters"] = dict(filters)
        return original(events_dir_arg, query, filters, store=store, limit=limit)

    with mock.patch.object(api_module, "run_search", capturing):
        res = client.get(
            "/v1/search",
            params={
                "q": "needle",
                "role": "worker",
                "model": "m1",
                "provider": "local",
                "cwd": "/workspace",
                "status": "completed",
                "session_id": "s1",
            },
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert res.status_code == 200
        assert seen["filters"] == {
            "role": ["worker"],
            "model": ["m1"],
            "provider": ["local"],
            "cwd": ["/workspace"],
            "status": ["completed"],
            "session_id": ["s1"],
        }
    client.__exit__(None, None, None)


def test_search_offloaded_to_a_worker_thread(events_dir: Path, settings: Settings) -> None:
    # The endpoint must not run the (blocking) search on the event loop; it must offload it
    # to a worker thread via asyncio.to_thread. Record which thread the search runs on.
    thread_names = []
    rows = store_rows(events_dir, [(f"run{i:03d}", {}) for i in range(5)])
    client = _client(settings)
    client.__enter__()
    original = search_module.search

    def capturing(*args, **kwargs):
        thread_names.append(threading.current_thread().name)
        return original(*args, **kwargs)

    with (
        mock.patch.object(api_module, "run_search", capturing),
        mock.patch.object(client.app.state.manager, "store", FakeStore(rows)),
    ):
        res = client.get(
            "/v1/search",
            params={"q": "needle"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert res.status_code == 200
    client.__exit__(None, None, None)
    # the search ran off the event loop (a worker thread), not on the loop thread
    assert thread_names
    assert all(name != "MainThread" for name in thread_names)
