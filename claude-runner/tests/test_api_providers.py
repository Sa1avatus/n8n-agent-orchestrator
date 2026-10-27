"""GET /v1/providers and POST /v1/providers/reload: the flag-off golden against the env blocks,
the flag-on merge with a database row, the reload picking up a new row, and no secret value in
any response. Keys are made up."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings, _providers
from claude_runner.provider_secrets import ProviderSecrets

FAKE_ENV = {
    "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_BASE_URL": "http://litellm:4000",
    "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_AUTH_TOKEN": "sk-made-up-env",
    "CLAUDE_RUNNER_PROVIDER_LOCAL__TOOLS": "Bash Read",
    "CLAUDE_RUNNER_PROVIDER_OTHER__ANTHROPIC_BASE_URL": "http://other:4000",
}
DB_KEY = "sk-made-up-db-0001"
GOLDEN_FLAG_OFF = {
    "providers": [
        {
            "name": "LOCAL",
            "api_type": "anthropic",
            "route": "direct",
            "models": [],
            "key_state": {"has_key": True, "set_at": None},
        },
        {
            "name": "OTHER",
            "api_type": "anthropic",
            "route": "direct",
            "models": [],
            "key_state": {"has_key": False, "set_at": None},
        },
    ]
}


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
        if self.failing_reads > 0:
            self.failing_reads -= 1
            raise RuntimeError("store down")
        return [dict(row) for row in self.rows]


def db_row(
    name: str,
    base_url: str,
    api_type: str = "anthropic",
    models: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "enabled": True,
        "base_url": base_url,
        "tools": "",
        "api_type": api_type,
        "models": models or [],
    }


def names(client: TestClient) -> list[str]:
    return [view["name"] for view in client.get("/v1/providers").json()["providers"]]


@pytest.fixture
def flag_off(settings: Settings) -> Iterator[TestClient]:
    s = replace(settings, providers=_providers(FAKE_ENV), providers_db=False)
    with TestClient(create_app(s)) as client:
        yield client


@pytest.fixture
def flag_on(settings: Settings, tmp_path: Path) -> Iterator[tuple[TestClient, FakeStore]]:
    secrets_file = tmp_path / "provider-secrets.json"
    ProviderSecrets(secrets_file).set_key("acme", DB_KEY)
    store = FakeStore([db_row("acme", "http://acme:4000")], stamp="s1")
    s = replace(
        settings,
        providers=_providers(FAKE_ENV),
        providers_db=True,
        secrets_file=secrets_file,
    )
    app = create_app(s)
    app.state.board_store = store
    with TestClient(app) as client:
        yield client, store


@pytest.fixture
def flag_on_openai(settings: Settings, tmp_path: Path) -> Iterator[tuple[TestClient, FakeStore]]:
    secrets_file = tmp_path / "provider-secrets.json"
    ProviderSecrets(secrets_file).set_key("acme", DB_KEY)
    row = db_row(
        "acme",
        "http://acme:4000",
        api_type="openai-chat",
        models=[{"model": "chat-model-1"}],
    )
    store = FakeStore([row], stamp="s1")
    s = replace(
        settings,
        providers=_providers(FAKE_ENV),
        providers_db=True,
        secrets_file=secrets_file,
    )
    app = create_app(s)
    app.state.board_store = store
    with TestClient(app) as client:
        yield client, store


@pytest.fixture
def flag_on_failing_store(
    settings: Settings, tmp_path: Path
) -> Iterator[tuple[TestClient, FakeStore]]:
    secrets_file = tmp_path / "provider-secrets.json"
    ProviderSecrets(secrets_file).set_key("acme", DB_KEY)
    store = FakeStore([db_row("acme", "http://acme:4000")], stamp="s1")
    store.failing_reads = 2  # both the stamp read and the list read raise at startup
    s = replace(
        settings,
        providers=_providers(FAKE_ENV),
        providers_db=True,
        secrets_file=secrets_file,
    )
    app = create_app(s)
    app.state.board_store = store
    with TestClient(app) as client:
        yield client, store


def test_flag_off_matches_the_golden(flag_off: TestClient) -> None:
    response = flag_off.get("/v1/providers")
    assert response.status_code == 200
    assert response.json() == GOLDEN_FLAG_OFF


def test_flag_off_reload_keeps_the_env_list(flag_off: TestClient) -> None:
    response = flag_off.post("/v1/providers/reload")
    assert response.status_code == 200
    assert flag_off.get("/v1/providers").json() == GOLDEN_FLAG_OFF


def test_responses_contain_no_secret_value(flag_off: TestClient) -> None:
    assert "sk-made-up-env" not in flag_off.get("/v1/providers").text


def test_flag_on_merges_the_database_row_without_its_key(
    flag_on: tuple[TestClient, FakeStore],
) -> None:
    client, _ = flag_on
    body = client.get("/v1/providers").json()
    assert sorted(view["name"] for view in body["providers"]) == ["ACME", "LOCAL", "OTHER"]
    acme = next(view for view in body["providers"] if view["name"] == "ACME")
    assert acme["key_state"]["has_key"] is True
    assert acme["key_state"]["set_at"] is not None
    assert acme["api_type"] == "anthropic"
    assert acme["models"] == []
    assert DB_KEY not in client.get("/v1/providers").text
    assert "sk-made-up-env" not in client.get("/v1/providers").text


def test_reload_picks_up_a_new_row(flag_on: tuple[TestClient, FakeStore]) -> None:
    client, store = flag_on
    assert "BETA" not in names(client)
    store.rows.append(db_row("beta", "http://beta:4000"))
    store.stamp = "s2"
    response = client.post("/v1/providers/reload")
    assert response.status_code == 200
    assert "BETA" in names(client)
    assert "BETA" in names(client)


def test_flag_on_openai_row_reports_route_models_and_key(
    flag_on_openai: tuple[TestClient, FakeStore],
) -> None:
    client, _ = flag_on_openai
    body = client.get("/v1/providers").json()
    acme = next(view for view in body["providers"] if view["name"] == "ACME")
    assert acme["api_type"] == "openai-chat"
    assert acme["route"] == "litellm"
    assert acme["models"] == ["chat-model-1"]
    assert acme["key_state"]["has_key"] is True
    assert acme["key_state"]["set_at"] is not None
    assert DB_KEY not in client.get("/v1/providers").text


def test_flag_on_failing_store_starts_with_the_env_list(
    flag_on_failing_store: tuple[TestClient, FakeStore],
) -> None:
    client, _ = flag_on_failing_store
    response = client.get("/v1/providers")
    assert response.status_code == 200
    assert response.json() == GOLDEN_FLAG_OFF


def test_env_proposals_list_the_env_blocks_without_key_values(flag_off: TestClient) -> None:
    rows = flag_off.get("/v1/providers/env-proposals").json()["providers"]
    assert rows
    assert all(set(row) == {"name", "base_url", "has_key"} for row in rows)
    assert "sk-made-up-env" not in str(rows)
