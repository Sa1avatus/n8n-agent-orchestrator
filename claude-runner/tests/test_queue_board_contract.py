"""Contract between the Kanban order and the shell: after a move the way the
Kanban sends it (POST /api/board/queue/move), the order GET /api/board/queue
returns is the order queue_board.next_mission picks missions in."""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.board import InMemoryBoardStore

QUEUE_BOARD_FILE = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "queue_board.py"
BASE = "http://testserver"
DASHBOARD_HEADERS = {"X-AHAWR-Dashboard": "1"}
MISSIONS = ("m_a", "m_b", "m_c", "m_d")


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


queue_board = _load("queue_board", QUEUE_BOARD_FILE)


def _opener(client: TestClient):
    """Serve queue_board's urlopen-style reads from the TestClient."""

    def open_url(url: str, timeout: float | None = None) -> io.BytesIO:
        response = client.get(url[len(BASE):])
        assert response.status_code == 200, response.text
        return io.BytesIO(response.content)

    return open_url


@pytest.fixture
def board(settings):
    store = InMemoryBoardStore()
    for mission_id in MISSIONS:
        store.add_queue(mission_id, None)
        store.upsert_live_state(mission_id, None, status="queued")
    app = create_app(settings)
    app.state.board_store = store
    with TestClient(app, base_url=BASE) as client:
        yield client


def _queue(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/board/queue")
    assert response.status_code == 200, response.text
    return response.json()


def _kanban_move(client: TestClient, mission_id: str, to_position: int) -> None:
    version = _queue(client)["version"]
    response = client.post(
        "/api/board/queue/move",
        json={"mission_id": mission_id, "to_position": to_position, "expected_version": version},
        headers=DASHBOARD_HEADERS,
    )
    assert response.status_code == 200, response.text


def _shell_picks(client: TestClient) -> list[str]:
    entries, states = queue_board.read_board(BASE, opener=_opener(client), max_tries=1)
    picked: list[str] = []
    for _ in range(len(entries)):
        pick = queue_board.next_mission(entries, states, set())
        if pick is None:
            break
        picked.append(pick["mission_id"])
        states[pick["mission_id"]] = queue_board.COMPLETED_STATE
    return picked


@pytest.mark.parametrize(
    ("mission_id", "to_position", "expected"),
    [
        ("m_d", 0, ["m_d", "m_a", "m_b", "m_c"]),
        ("m_a", 3, ["m_b", "m_c", "m_d", "m_a"]),
        ("m_b", 2, ["m_a", "m_c", "m_b", "m_d"]),
    ],
)
def test_shell_picks_in_the_order_the_kanban_move_set(
    board: TestClient, mission_id: str, to_position: int, expected: list[str]
) -> None:
    _kanban_move(board, mission_id, to_position)

    payload = _queue(board)
    kanban_order = [entry["mission_id"] for entry in payload["queue"]]
    assert kanban_order == expected
    assert [entry["position"] for entry in payload["queue"]] == [0, 1, 2, 3]

    assert _shell_picks(board) == kanban_order


def test_shell_skips_done_missions_in_the_moved_order(board: TestClient) -> None:
    _kanban_move(board, "m_d", 0)
    board.app.state.board_store.upsert_live_state("m_a", None, status="completed")
    board.app.state.board_store.upsert_live_state("m_d", None, status="failed")

    assert _shell_picks(board) == ["m_b", "m_c"]
