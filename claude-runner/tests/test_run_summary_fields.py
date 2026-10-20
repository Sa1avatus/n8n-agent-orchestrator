from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from claude_runner.run_views import MAX_FILES_CHANGED, build_router, summary

CWD = "/work/project"


def _run(run_id: str = "r1", cwd: str = CWD) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "kind": "run",
        "input": "do it",
        "status": "succeeded",
        "role": "worker",
        "model": "m",
        "provider": "",
        "session_id": "s1",
        "claude_session_id": "",
        "cwd": cwd,
        "created_at": "2026-01-01T00:00:00",
        "started_at": "2026-01-01T00:00:01",
        "finished_at": "2026-01-01T00:01:00",
        "num_turns": 2,
        "cost_usd": 0.1,
        "context_tokens": 10,
        "details": {},
        "error_code": None,
        "error_message": None,
    }


def _edit(name: str, file_path: str) -> dict[str, Any]:
    return {"kind": "tool_use", "name": name, "input": {"file_path": file_path}}


class _Events:
    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries

    def read(self, run_id: str, after: int = 0) -> tuple[list[dict[str, Any]], None]:
        return self.entries[after:], None

    def is_live(self, run_id: str) -> bool:
        return False


class _Store:
    def __init__(self, runs: list[dict[str, Any]]) -> None:
        self.runs = {run["run_id"]: run for run in runs}

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    def list_runs(self, limit: int, *_: Any) -> list[dict[str, Any]]:
        return list(self.runs.values())[:limit]


class _Manager:
    def __init__(self, store: _Store, events: _Events) -> None:
        self.store = store
        self.events = events


def _manager(entries: list[dict[str, Any]]) -> _Manager:
    return _Manager(_Store([_run()]), _Events(entries))


def test_counts_compactions_steps_and_edits() -> None:
    entries = [
        {"kind": "step"},
        {"kind": "compact"},
        {"kind": "step"},
        _edit("Edit", f"{CWD}/a.py"),
        _edit("Write", f"{CWD}/b.py"),
        _edit("Bash", f"{CWD}/c.py"),
    ]
    out = summary(_run(), _manager(entries))
    assert out["compactions"] == 1
    assert out["steps"] == 2
    assert out["edits"] == 2
    assert out["files_changed"] == ["a.py", "b.py"]


def test_files_changed_unique_relative_and_capped() -> None:
    entries = [
        _edit("Edit", f"{CWD}/a.py"),
        _edit("Edit", f"{CWD}/a.py"),
        _edit("Write", "rel/x.py"),
    ]
    entries += [_edit("Write", f"{CWD}/f{i}.py") for i in range(60)]
    out = summary(_run(), _manager(entries))
    assert out["files_changed"][:3] == ["a.py", "rel/x.py", "f0.py"]
    assert len(out["files_changed"]) == MAX_FILES_CHANGED
    assert len(set(out["files_changed"])) == MAX_FILES_CHANGED


def test_paths_outside_cwd_stay_as_given() -> None:
    out = summary(_run(), _manager([_edit("Edit", "/etc/hosts")]))
    assert out["files_changed"] == ["/etc/hosts"]


def test_run_without_events_has_zero_fields() -> None:
    out = summary(_run(), _manager([]))
    assert (out["compactions"], out["steps"], out["edits"], out["files_changed"]) == (0, 0, 0, [])


def test_existing_fields_are_kept() -> None:
    out = summary(_run(), _manager([]))
    assert out["run_id"] == "r1"
    assert out["status"] == "succeeded"
    assert out["cwd"] == CWD
    assert out["live"] is False


def test_list_and_single_run_endpoints_carry_the_fields() -> None:
    entries = [{"kind": "step"}, {"kind": "compact"}, _edit("Edit", f"{CWD}/a.py")]
    manager = _manager(entries)
    app = FastAPI()
    app.include_router(build_router(lambda: manager))
    client = TestClient(app)

    listed = client.get("/runs").json()["runs"][0]
    single = client.get("/runs/r1/events").json()["run"]
    for out in (listed, single):
        assert out["compactions"] == 1
        assert out["steps"] == 1
        assert out["edits"] == 1
        assert out["files_changed"] == ["a.py"]
