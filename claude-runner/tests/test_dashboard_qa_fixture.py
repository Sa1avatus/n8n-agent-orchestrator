from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from claude_runner import board_api, dashboard
from claude_runner.board import get_board_store
from claude_runner.board_pg import DSN_ENV, UnavailableBoardStore
from claude_runner.dashboard import QA_FIXTURE_ENV, DashboardSettings, create_dashboard_app
from claude_runner.qa_fixture import MISSIONS_TABLE, QUEUE_TABLE

FIXTURE = Path(__file__).resolve().parents[1] / "qa" / "fixtures" / "default.json"
DASHBOARD_HEADER = {"X-AHAWR-Dashboard": "1"}


def fixture_tables() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8"))["tables"])


@pytest.fixture
def fixture_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(QA_FIXTURE_ENV, str(FIXTURE))
    monkeypatch.delenv(DSN_ENV, raising=False)
    return TestClient(create_dashboard_app(DashboardSettings()), base_url="http://localhost")


def queue_ids(client: TestClient) -> list[str]:
    response = client.get("/api/board/queue")
    assert response.status_code == 200, response.text
    return [entry["mission_id"] for entry in response.json()["queue"]]


def test_fixture_counts_are_served(fixture_client: TestClient) -> None:
    tables = fixture_tables()
    with fixture_client as client:
        summary = client.get("/api/board/summary")
        missions = client.get("/api/board/missions")
        queue = client.get("/api/board/queue")
        assert summary.status_code == 200, summary.text
        assert missions.status_code == 200, missions.text
        assert queue.status_code == 200, queue.text
        assert len(summary.json()["missions"]) == len(tables[MISSIONS_TABLE])
        assert len(missions.json()["missions"]) == len(tables[MISSIONS_TABLE])
        assert len(queue.json()["queue"]) == len(tables[QUEUE_TABLE])


def test_queue_move_with_header_changes_order(fixture_client: TestClient) -> None:
    with fixture_client as client:
        before = queue_ids(client)
        moved = client.post(
            "/api/board/queue/move",
            json={"mission_id": "m03", "to_position": 0},
            headers=DASHBOARD_HEADER,
        )
        assert moved.status_code == 200, moved.text
        expected = ["m03"] + [mission_id for mission_id in before if mission_id != "m03"]
        assert queue_ids(client) == expected
        assert expected != before


def test_queue_move_without_header_is_refused(fixture_client: TestClient) -> None:
    with fixture_client as client:
        before = queue_ids(client)
        refused = client.post(
            "/api/board/queue/move",
            json={"mission_id": "m03", "to_position": 0},
        )
        assert refused.status_code == 403
        assert refused.json() == {"error": "missing X-AHAWR-Dashboard header"}
        assert queue_ids(client) == before


def test_fixture_refused_together_with_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(QA_FIXTURE_ENV, str(FIXTURE))
    monkeypatch.setenv(DSN_ENV, "postgresql://user:pw@db/board")
    with pytest.raises(RuntimeError, match=f"{QA_FIXTURE_ENV} and {DSN_ENV}"):
        create_dashboard_app(DashboardSettings())


def test_unset_fixture_keeps_store_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(QA_FIXTURE_ENV, raising=False)
    monkeypatch.delenv(DSN_ENV, raising=False)
    real_get_board_store = get_board_store
    store_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def spy_get_board_store(*args: Any, **kwargs: Any) -> Any:
        store_calls.append((args, kwargs))
        return real_get_board_store(*args, **kwargs)

    def no_fixture_load(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the QA fixture must not be loaded when the setting is unset")

    monkeypatch.setattr(board_api, "get_board_store", spy_get_board_store)
    monkeypatch.setattr(dashboard, "board_store_from_rows", no_fixture_load)
    app = create_dashboard_app(DashboardSettings())
    assert store_calls == [((), {})]
    assert isinstance(app.state.board_store, UnavailableBoardStore)
    assert app.state.board_store.status()[0] == "unavailable"
