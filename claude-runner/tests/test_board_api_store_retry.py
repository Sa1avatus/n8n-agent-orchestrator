"""The board store is rebuilt after the database comes up (live_store)."""
from __future__ import annotations

from types import SimpleNamespace

from claude_runner import board_api
from claude_runner.board import InMemoryBoardStore
from claude_runner.board_pg import UnavailableBoardStore


def _app(store: object) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(board_store=store))


def test_unavailable_store_is_rebuilt_once_the_database_is_up(monkeypatch) -> None:
    calls: list[int] = []
    healthy = InMemoryBoardStore()

    def build() -> object:
        calls.append(1)
        return healthy

    monkeypatch.setattr(board_api, "get_board_store", build)
    app = _app(UnavailableBoardStore("the database is unavailable: starting up"))
    assert board_api.live_store(app) is healthy
    assert calls == [1]


def test_retry_is_rate_limited(monkeypatch) -> None:
    calls: list[int] = []

    def build() -> object:
        calls.append(1)
        return UnavailableBoardStore("still down")

    monkeypatch.setattr(board_api, "get_board_store", build)
    app = _app(UnavailableBoardStore("down"))
    board_api.live_store(app)
    board_api.live_store(app)
    assert calls == [1]
    app.state.board_store_tried -= board_api.STORE_RETRY_SECONDS
    board_api.live_store(app)
    assert calls == [1, 1]


def test_available_store_is_returned_untouched(monkeypatch) -> None:
    monkeypatch.setattr(board_api, "get_board_store", lambda: 1 / 0)
    store = InMemoryBoardStore()
    assert board_api.live_store(_app(store)) is store
