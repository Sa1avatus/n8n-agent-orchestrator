"""Provider store methods of InMemoryBoardStore and PgBoardStore: CRUD on
providers, the optimistic version check (409), the model cascade on delete, the
reload stamp, and one changelog row per write that carries no secret.  The pg
variant needs AHAWR_TEST_BOARD_PG_DSN, a database whose name contains "test"
(the schema is applied and the provider tables are emptied); without it the pg
variant is skipped."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from claude_runner.board import ConflictError, InMemoryBoardStore
from claude_runner.board_pg import _HERMES_TABLE, _LIVE_STATE_TABLE, PgBoardStore

TEST_DSN_ENV = "AHAWR_TEST_BOARD_PG_DSN"
SCHEMA_FILE = Path(__file__).resolve().parents[1] / "docs" / "board-schema.sql"
Store = InMemoryBoardStore | PgBoardStore


def _pg_skip_reason() -> str | None:
    if not os.environ.get(TEST_DSN_ENV):
        return f"{TEST_DSN_ENV} is not set: no test database for PgBoardStore"
    try:
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        return "psycopg is not installed"
    dbname = conninfo_to_dict(os.environ[TEST_DSN_ENV]).get("dbname") or ""
    if "test" not in dbname:
        return f"{TEST_DSN_ENV} must name a database whose name contains 'test'"
    return None


def _fresh_pg_store() -> PgBoardStore:
    import psycopg

    dsn = os.environ[TEST_DSN_ENV]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        for table in (_LIVE_STATE_TABLE, _HERMES_TABLE):
            conn.execute(f'CREATE TABLE IF NOT EXISTS {table} ("updatedAt" timestamptz)')
        conn.execute(
            "TRUNCATE board.provider_models, board.providers, board.changelog RESTART IDENTITY"
        )
    return PgBoardStore(dsn)


@pytest.fixture(params=["memory", "pg"])
def store(request: pytest.FixtureRequest) -> Iterator[Store]:
    if request.param == "memory":
        yield InMemoryBoardStore()
        return
    reason = _pg_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    pg = _fresh_pg_store()
    yield pg
    pg._conn.close()


def _chronological(store: Store) -> list[str]:
    return [entry.action for entry in reversed(store.changelog())]


def _model_version(store: Store) -> str:
    return store.get_provider("acme")["models"][0]["version"]


class TestProviderCrud:
    def test_create_then_get_and_list(self, store: Store):
        store.create_provider("acme", {"label": "Acme", "base_url": "https://a.example"})
        provider = store.get_provider("acme")
        assert provider is not None
        assert provider["label"] == "Acme"
        assert provider["enabled"] is True
        assert provider["models"] == []
        assert provider["version"]
        assert [p["name"] for p in store.list_providers()] == ["acme"]

    def test_get_missing_is_none(self, store: Store):
        assert store.get_provider("nope") is None

    def test_create_twice_is_a_conflict(self, store: Store):
        store.create_provider("acme", {})
        with pytest.raises(ConflictError):
            store.create_provider("acme", {})

    def test_update_with_current_version(self, store: Store):
        store.create_provider("acme", {"label": "Acme"})
        before = store.get_provider("acme")
        assert before is not None
        store.update_provider("acme", {"label": "Acme 2", "enabled": False}, before["version"])
        after = store.get_provider("acme")
        assert after is not None
        assert after["label"] == "Acme 2"
        assert after["enabled"] is False
        assert after["version"] != before["version"]

    def test_stale_version_is_a_conflict(self, store: Store):
        store.create_provider("acme", {"label": "Acme"})
        stale = store.get_provider("acme")["version"]
        store.update_provider("acme", {"label": "first"}, stale)
        with pytest.raises(ConflictError):
            store.update_provider("acme", {"label": "second"}, stale)
        assert store.get_provider("acme")["label"] == "first"

    def test_update_missing_raises_key_error(self, store: Store):
        with pytest.raises(KeyError):
            store.update_provider("nope", {}, None)

    def test_delete_with_stale_version_is_a_conflict(self, store: Store):
        store.create_provider("acme", {})
        stale = store.get_provider("acme")["version"]
        store.update_provider("acme", {"label": "x"}, stale)
        with pytest.raises(ConflictError):
            store.delete_provider("acme", stale)
        assert store.get_provider("acme") is not None

    def test_delete_removes_provider(self, store: Store):
        store.create_provider("acme", {})
        store.delete_provider("acme", store.get_provider("acme")["version"])
        assert store.get_provider("acme") is None
        assert store.list_providers() == []


class TestSecretsDropped:
    def test_secret_field_is_not_stored_or_logged(self, store: Store):
        store.create_provider("acme", {"base_url": "https://a.example", "api_key": "sk-SECRET-1"})
        version = store.get_provider("acme")["version"]
        store.update_provider("acme", {"auth_token": "tok-SECRET-2"}, version)
        provider = store.get_provider("acme")
        assert "api_key" not in provider
        assert "auth_token" not in provider
        dump = json.dumps([entry.model_dump() for entry in store.changelog()])
        assert "SECRET" not in dump


class TestModels:
    def test_add_model_shows_under_provider(self, store: Store):
        store.create_provider("acme", {})
        store.add_model("acme", "m1", {"price_input_per_mtok_usd": 1.5, "alias": "fast"})
        models = store.get_provider("acme")["models"]
        assert [m["model"] for m in models] == ["m1"]
        assert models[0]["alias"] == "fast"
        assert models[0]["probe_result"] is None
        assert models[0]["supports_thinking"] is False

    def test_add_model_needs_provider(self, store: Store):
        with pytest.raises(KeyError):
            store.add_model("nope", "m1", {})

    def test_add_duplicate_model_is_a_conflict(self, store: Store):
        store.create_provider("acme", {})
        store.add_model("acme", "m1", {})
        with pytest.raises(ConflictError):
            store.add_model("acme", "m1", {})

    def test_update_model_stale_version_is_a_conflict(self, store: Store):
        store.create_provider("acme", {})
        store.add_model("acme", "m1", {"alias": "one"})
        stale = store.get_provider("acme")["models"][0]["version"]
        store.update_model("acme", "m1", {"alias": "two"}, stale)
        with pytest.raises(ConflictError):
            store.update_model("acme", "m1", {"alias": "three"}, stale)
        assert store.get_provider("acme")["models"][0]["alias"] == "two"

    def test_remove_model_keeps_the_provider_and_other_models(self, store: Store):
        store.create_provider("acme", {})
        store.add_model("acme", "m1", {})
        store.add_model("acme", "m2", {})
        store.remove_model("acme", "m1", store.get_provider("acme")["models"][0]["version"])
        assert [m["model"] for m in store.get_provider("acme")["models"]] == ["m2"]

    def test_set_model_probe_stores_result_and_time(self, store: Store):
        store.create_provider("acme", {})
        store.add_model("acme", "m1", {})
        store.set_model_probe("acme", "m1", {"status": "ok"})
        model = store.get_provider("acme")["models"][0]
        assert model["probe_result"] == {"status": "ok"}
        assert model["probed_at"] is not None

    def test_set_model_probe_missing_model_raises_key_error(self, store: Store):
        store.create_provider("acme", {})
        with pytest.raises(KeyError):
            store.set_model_probe("acme", "missing", {})


class TestCascade:
    def test_delete_provider_removes_its_models_only(self, store: Store):
        store.create_provider("acme", {})
        store.create_provider("other", {})
        store.add_model("acme", "m1", {})
        store.add_model("other", "m1", {})
        store.delete_provider("acme", store.get_provider("acme")["version"])
        assert [m["model"] for m in store.get_provider("other")["models"]] == ["m1"]
        store.create_provider("acme", {})
        assert store.get_provider("acme")["models"] == []

    def test_changelog_keeps_the_models_of_a_deleted_provider(self, store: Store):
        store.create_provider("acme", {})
        store.add_model("acme", "m1", {"alias": "one"})
        store.delete_provider("acme", store.get_provider("acme")["version"])
        remove = next(e for e in store.changelog() if e.action == "provider remove")
        assert [m["model"] for m in remove.before_json["models"]] == ["m1"]


class TestReloadStamp:
    def test_stamp_moves_on_every_write(self, store: Store):
        stamps = [store.providers_updated_at()]
        store.create_provider("acme", {})
        stamps.append(store.providers_updated_at())
        store.add_model("acme", "m1", {})
        stamps.append(store.providers_updated_at())
        store.set_model_probe("acme", "m1", {"status": "ok"})
        stamps.append(store.providers_updated_at())
        store.delete_provider("acme", store.get_provider("acme")["version"])
        stamps.append(store.providers_updated_at())
        assert stamps[0] == ""
        assert len(set(stamps)) == len(stamps)

    def test_refused_write_does_not_move_the_stamp(self, store: Store):
        store.create_provider("acme", {})
        before = store.providers_updated_at()
        with pytest.raises(ConflictError):
            store.update_provider("acme", {"label": "x"}, "stale")
        assert store.providers_updated_at() == before


class TestChangelog:
    def test_one_row_per_write_in_order(self, store: Store):
        store.create_provider("acme", {"label": "Acme"})
        version = store.get_provider("acme")["version"]
        store.update_provider("acme", {"label": "Acme 2"}, version)
        store.add_model("acme", "m1", {})
        store.update_model("acme", "m1", {"alias": "a"}, _model_version(store))
        store.set_model_probe("acme", "m1", {"status": "ok"})
        store.remove_model("acme", "m1", _model_version(store))
        store.delete_provider("acme", store.get_provider("acme")["version"])
        assert _chronological(store) == [
            "provider create",
            "provider update",
            "provider model add",
            "provider model update",
            "provider model probe",
            "provider model remove",
            "provider remove",
        ]

    def test_update_row_keeps_the_previous_values(self, store: Store):
        store.create_provider("acme", {"label": "Acme"})
        store.update_provider("acme", {"label": "Acme 2"}, store.get_provider("acme")["version"])
        update = next(e for e in store.changelog() if e.action == "provider update")
        assert update.before_json["label"] == "Acme"
        assert update.after_json["label"] == "Acme 2"

    def test_refused_write_writes_no_row(self, store: Store):
        store.create_provider("acme", {})
        count = len(store.changelog())
        with pytest.raises(ConflictError):
            store.update_provider("acme", {"label": "x"}, "stale")
        assert len(store.changelog()) == count
