from __future__ import annotations

from fastapi.testclient import TestClient

from claude_runner.board import InMemoryBoardStore, Mission

from .conftest import wait_for
from .test_dashboard import dashboard
from .test_runs import start


def _board(linked_run_id: str) -> InMemoryBoardStore:
    board = InMemoryBoardStore()
    board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1", name="Mission")
    board.append_event(
        "sk1",
        {
            "state_key": "sk1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "event_type": "worker_completed",
            "worker_run_id": linked_run_id,
        },
    )
    return board


def test_runs_filtered_by_mission(client: TestClient) -> None:
    linked = start(client, role="worker", input="linked")
    other = start(client, role="worker", input="other")
    wait_for(client, linked["run_id"])
    wait_for(client, other["run_id"])
    with dashboard(client) as dash:
        dash.app.state.board_store = _board(linked["run_id"])

        everything = dash.get("/api/runs").json()
        assert set(everything) == {"runs", "sources"}
        ids = {r["run_id"] for r in everything["runs"]}
        assert {linked["run_id"], other["run_id"]} <= ids

        mine = dash.get("/api/runs", params={"mission": "m1"}).json()
        assert [r["run_id"] for r in mine["runs"]] == [linked["run_id"]]

        assert dash.get("/api/runs", params={"mission": "nope"}).json()["runs"] == []
        assert dash.get("/api/runs", params={"mission": ""}).json()["runs"] == everything["runs"]
