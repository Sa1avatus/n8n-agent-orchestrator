"""The board store is rebuilt after the database comes up (live_store)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ahawr_runner import board_api
from ahawr_runner.board import InMemoryBoardStore
from ahawr_runner.board_pg import UnavailableBoardStore


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


class _UpStore:
    def providers_updated_at(self) -> str:
        return "stamp-1"

    def list_providers(self) -> list[dict[str, object]]:
        return [{"name": "strata", "enabled": True, "base_url": "http://strata:8080", "models": []}]


def test_provider_registry_loads_the_database_providers_after_the_database_comes_up(
    monkeypatch, tmp_path
) -> None:
    from ahawr_runner.provider_registry import ProviderRegistry
    from ahawr_runner.provider_secrets import ProviderSecrets

    app = _app(UnavailableBoardStore("the database is unavailable: starting up"))
    registry = ProviderRegistry(
        {},
        providers_db=True,
        store=board_api.LiveProviderStore(app),
        secrets=ProviderSecrets(tmp_path / "keys.json"),
    )
    assert "STRATA" not in registry.profiles()  # the database was down at the start
    monkeypatch.setattr(board_api, "get_board_store", lambda: _UpStore())
    app.state.board_store_tried = float("-inf")
    assert registry.refresh_if_changed() is True
    assert "STRATA" in registry.profiles()


def test_unavailable_store_refuses_providers_updated_at_with_the_reason() -> None:
    store = UnavailableBoardStore("the database is unavailable: starting up")
    with pytest.raises(RuntimeError, match="starting up"):
        store.providers_updated_at()
