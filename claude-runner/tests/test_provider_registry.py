"""ProviderRegistry (claude_runner/provider_registry.py): the flag-off golden against the
legacy env parser, the database override, reload on a stamp change, the poll loop's
backoff and error logging, and secrets kept out of repr and logs. Keys are made up."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import pytest

from claude_runner.config import ProviderProfile, _providers
from claude_runner.provider_registry import (
    RELOAD_MAX_BACKOFF_SECONDS,
    ProviderRegistry,
    poll_delay_seconds,
)
from claude_runner.provider_secrets import ProviderSecrets

FAKE_ENV = {
    "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_BASE_URL": "http://litellm:4000",
    "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_AUTH_TOKEN": "sk-made-up-env",
    "CLAUDE_RUNNER_PROVIDER_LOCAL__TOOLS": "Bash Read",
    "CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_PCT": "70",
    "CLAUDE_RUNNER_PROVIDER_OTHER__ANTHROPIC_BASE_URL": "http://other:4000",
}
DB_KEY = "sk-made-up-db-0001"


class FakeStore:
    def __init__(self, rows: list[dict[str, Any]], stamp: str) -> None:
        self.rows = rows
        self.stamp = stamp
        self.failing_reads = 0

    def providers_updated_at(self) -> str:
        if self.failing_reads > 0:
            self.failing_reads -= 1
            raise RuntimeError("store down")
        return self.stamp

    def list_providers(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]


class UnusedStore:
    def providers_updated_at(self) -> str:
        raise AssertionError("the store must not be read with providers_db off")

    def list_providers(self) -> list[dict[str, Any]]:
        raise AssertionError("the store must not be read with providers_db off")


class StopAfterWaits(threading.Event):
    def __init__(self, waits: int) -> None:
        super().__init__()
        self.delays: list[float | None] = []
        self._left = waits

    def wait(self, timeout: float | None = None) -> bool:
        self.delays.append(timeout)
        self._left -= 1
        if self._left == 0:
            self.set()
        return self.is_set()


def db_row(name: str, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": name,
        "enabled": True,
        "base_url": "",
        "tools": "",
        "compact_pct": None,
        "compact_min_pct": None,
        "max_output_tokens": None,
        "search_first": None,
        "retry_fresh_session": None,
    }
    return {**row, **fields}


@pytest.fixture
def secrets(tmp_path: Path) -> ProviderSecrets:
    store = ProviderSecrets(tmp_path / "provider-secrets.json")
    store.set_key("local", DB_KEY)
    return store


def test_flag_off_matches_legacy_env_parser(tmp_path: Path, secrets: ProviderSecrets) -> None:
    registry = ProviderRegistry(
        _providers(FAKE_ENV), providers_db=False, store=UnusedStore(), secrets=secrets
    )
    assert registry.profiles() == _providers(FAKE_ENV)
    assert registry.refresh_if_changed() is False
    assert registry.start_polling() is None
    registry.reload()
    assert registry.profiles() == _providers(FAKE_ENV)


def test_database_row_overrides_env_block_of_same_name(secrets: ProviderSecrets) -> None:
    store = FakeStore(
        [
            db_row("local", base_url="http://db:4000", tools="Read", compact_pct=60.0),
            db_row("acme", base_url="http://acme:4000"),
        ],
        stamp="2026-10-26T10:00:00+00:00",
    )
    registry = ProviderRegistry(
        _providers(FAKE_ENV), providers_db=True, store=store, secrets=secrets
    )
    profiles = registry.profiles()
    assert profiles["LOCAL"] == ProviderProfile(
        name="LOCAL",
        env={"ANTHROPIC_BASE_URL": "http://db:4000", "ANTHROPIC_AUTH_TOKEN": DB_KEY},
        tools="Read",
        compact_pct=60.0,
    )
    assert profiles["OTHER"] == _providers(FAKE_ENV)["OTHER"]
    assert profiles["ACME"].env == {"ANTHROPIC_BASE_URL": "http://acme:4000"}


def test_disabled_database_row_is_not_built(secrets: ProviderSecrets) -> None:
    store = FakeStore([db_row("local", enabled=False, base_url="http://db:4000")], stamp="s1")
    registry = ProviderRegistry(
        _providers(FAKE_ENV), providers_db=True, store=store, secrets=secrets
    )
    assert registry.profiles()["LOCAL"] == _providers(FAKE_ENV)["LOCAL"]


def test_reload_picks_up_a_stamp_change(secrets: ProviderSecrets) -> None:
    store = FakeStore([db_row("acme", base_url="http://acme:4000")], stamp="s1")
    registry = ProviderRegistry({}, providers_db=True, store=store, secrets=secrets)
    assert registry.refresh_if_changed() is False

    store.rows.append(db_row("beta", base_url="http://beta:4000"))
    store.stamp = "s2"
    assert registry.refresh_if_changed() is True
    assert set(registry.profiles()) == {"ACME", "BETA"}
    assert registry.refresh_if_changed() is False


def test_poll_loop_backs_off_on_store_errors_and_logs_them(
    secrets: ProviderSecrets, caplog: pytest.LogCaptureFixture
) -> None:
    store = FakeStore([], stamp="s1")
    registry = ProviderRegistry({}, providers_db=True, store=store, secrets=secrets)
    store.failing_reads = 2
    stop = StopAfterWaits(waits=3)
    with caplog.at_level(logging.WARNING, logger="claude_runner.provider_registry"):
        registry.run_poll_loop(stop)

    assert stop.delays == [60, 120, 30]
    assert caplog.text.count("provider reload failed") == 2
    assert "store down" in caplog.text


def test_poll_delay_is_capped() -> None:
    assert poll_delay_seconds(30, 0) == 30
    assert poll_delay_seconds(30, 1) == 60
    assert poll_delay_seconds(30, 50) == RELOAD_MAX_BACKOFF_SECONDS


def test_secrets_stay_out_of_repr_and_logs(
    secrets: ProviderSecrets, caplog: pytest.LogCaptureFixture
) -> None:
    store = FakeStore([db_row("local", base_url="http://db:4000")], stamp="s1")
    with caplog.at_level(logging.DEBUG):
        registry = ProviderRegistry(
            _providers(FAKE_ENV), providers_db=True, store=store, secrets=secrets
        )
        store.stamp = "s2"
        registry.refresh_if_changed()
        registry.reload()
    assert DB_KEY not in repr(registry)
    assert DB_KEY not in caplog.text
    assert "sk-made-up-env" not in caplog.text


def test_db_profile_serves_database_only_providers_and_leaves_env_blocks_alone(
    secrets: ProviderSecrets,
) -> None:
    store = FakeStore(
        [db_row("local", base_url="http://db:4000"), db_row("acme", base_url="http://acme:4000")],
        stamp="2026-10-26T10:00:00+00:00",
    )
    registry = ProviderRegistry(
        _providers(FAKE_ENV), providers_db=True, store=store, secrets=secrets
    )
    acme = registry.db_profile("acme")
    assert acme is not None and acme.env["ANTHROPIC_BASE_URL"] == "http://acme:4000"
    assert registry.db_profile("local") is None
    assert registry.db_profile("nowhere") is None


def test_a_run_falls_back_to_a_database_provider_only_without_an_env_block() -> None:
    from types import SimpleNamespace

    from claude_runner.runs import RunManager

    env_profile = ProviderProfile(name="LOCAL", env={})
    db_profile = ProviderProfile(name="ACME", env={"ANTHROPIC_BASE_URL": "http://acme:4000"})
    manager = SimpleNamespace(
        settings=SimpleNamespace(provider=lambda name: env_profile if name == "local" else None),
        extra_provider=lambda name: db_profile,
    )
    assert RunManager._provider(manager, "local") is env_profile  # type: ignore[arg-type]
    assert RunManager._provider(manager, "acme") is db_profile  # type: ignore[arg-type]
    assert RunManager._provider(manager, "  ") is None  # type: ignore[arg-type]
