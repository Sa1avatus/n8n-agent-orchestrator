"""Provider routes (claude_runner/board_providers_api.py) on the in-memory store and a temp
secrets file: validation at the boundary, the version check and the dry-run diff, the refused
delete while a role uses the provider, and the models.  No response may carry a key."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from claude_runner.board import InMemoryBoardStore, Mission
from claude_runner.board_api import add_board_routes
from claude_runner.board_providers_api import add_provider_routes
from claude_runner.config import ProviderProfile
from claude_runner.provider_secrets import ProviderSecrets

PREFIX = "/api/board/providers"
HEADERS = {"X-AHAWR-Dashboard": "1"}
SECRET = "sk-test-0000-made-up-key"
PROVIDER = {
    "name": "made-up",
    "label": "Made up",
    "api_type": "openai-chat",
    "base_url": "https://made-up.example/v1",
}


class FakePost:
    """Stands in for the HTTP client; records every call and answers with ``status_for``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, Any], float]] = []
        self.status_for: Callable[[dict[str, Any]], int] = lambda body: 200
        self.error: Exception | None = None

    def __call__(
        self, url: str, headers: dict[str, str], body: dict[str, Any], timeout: float
    ) -> int:
        self.calls.append((url, headers, body, timeout))
        if self.error is not None:
            raise self.error
        return self.status_for(body)


@pytest.fixture
def fake_post(monkeypatch: pytest.MonkeyPatch) -> FakePost:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a test reached the real network")

    monkeypatch.setattr(httpx, "post", refuse)
    return FakePost()


def _make_board(
    tmp_path: Path,
    post: FakePost,
    providers: dict[str, ProviderProfile] | None = None,
) -> tuple[TestClient, InMemoryBoardStore, ProviderSecrets]:
    store = InMemoryBoardStore()
    app = FastAPI()
    app.state.board_store = store
    add_board_routes(app, SimpleNamespace(providers={}))
    secrets = ProviderSecrets(tmp_path / "provider_keys.json")
    add_provider_routes(app, secrets, providers=providers, post=post)
    return TestClient(app), store, secrets


@pytest.fixture
def board(
    tmp_path: Path, fake_post: FakePost
) -> tuple[TestClient, InMemoryBoardStore, ProviderSecrets]:
    return _make_board(tmp_path, fake_post)


def _create(client: TestClient, **overrides: Any) -> Any:
    return client.post(PREFIX, json={**PROVIDER, **overrides}, headers=HEADERS)


def _version(client: TestClient, name: str = "made-up") -> str:
    return str(client.get(f"{PREFIX}/{name}").json()["provider"]["version"])


def _model_version(client: TestClient, model: str, name: str = "made-up") -> str:
    models = client.get(f"{PREFIX}/{name}").json()["provider"]["models"]
    return next(m["version"] for m in models if m["model"] == model)


def _key_fields(value: Any) -> list[str]:
    if isinstance(value, dict):
        found = [k for k in value if k.lower().endswith("key") and k != "has_key"]
        return found + [f for v in value.values() for f in _key_fields(v)]
    if isinstance(value, list):
        return [f for v in value for f in _key_fields(v)]
    return []


def _seed_mission(store: InMemoryBoardStore, roles: dict[str, Any]) -> None:
    store._missions["m1"] = Mission(
        mission_id="m1", state_namespace="sk1", name="Source",
        working_directory="/tmp/x", rules=["r1"],
        acceptance_criteria=["c1"], retrieval_corpora=["corpus"],
        updated_at="2026-01-01T00:00:00+00:00",
    )
    store.set_mission_config("m1", {"roles_json": json.dumps(roles)}, None)


def test_create_then_get_reports_key_state_without_the_key(board) -> None:
    client, _, secrets = board
    secrets.set_key("made-up", SECRET)
    assert _create(client).status_code == 200
    provider = client.get(f"{PREFIX}/made-up").json()["provider"]
    assert provider["key_state"]["has_key"] is True
    assert provider["key_state"]["set_at"]
    assert _key_fields(provider) == []
    assert SECRET not in client.get(PREFIX).text


def test_key_state_is_empty_without_a_key(board) -> None:
    client, _, _ = board
    _create(client)
    providers = client.get(PREFIX).json()["providers"]
    assert providers[0]["key_state"] == {"has_key": False, "set_at": None}


@pytest.mark.parametrize("name", ["Bad Name", "-leading", "a/b", "x" * 65, ""])
def test_create_rejects_a_bad_name(board, name: str) -> None:
    client, store, _ = board
    assert _create(client, name=name).status_code == 400
    assert store.list_providers() == []


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://made-up.example/v1",
        "https://user:pass@made-up.example/v1",
        "https://",
        "javascript:alert(1)",
    ],
)
def test_create_rejects_a_bad_base_url(board, base_url: str) -> None:
    client, store, _ = board
    assert _create(client, base_url=base_url).status_code == 400
    assert store.list_providers() == []


def test_create_accepts_a_local_http_base_url(board) -> None:
    client, _, _ = board
    assert _create(client, base_url="http://localhost:11434/v1").status_code == 200


def test_create_rejects_an_unknown_api_type(board) -> None:
    client, _, _ = board
    response = _create(client, api_type="no-such-api")
    assert response.status_code == 400
    assert "unknown api_type" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("context_tokens", 0),
        ("context_tokens", True),
        ("compact_pct", 101),
        ("compact_min_pct", 0),
        ("max_output_tokens", 1.5),
        ("tools", 5),
    ],
)
def test_create_rejects_out_of_range_or_wrong_type(board, field: str, value: Any) -> None:
    client, _, _ = board
    assert _create(client, **{field: value}).status_code == 400


def test_unknown_fields_are_refused_and_a_key_is_never_stored(board) -> None:
    client, store, secrets = board
    assert _create(client, key=SECRET).status_code == 400
    assert store.list_providers() == []
    assert secrets.key_state("made-up").has_key is False


def test_write_without_the_dashboard_header_is_refused(board) -> None:
    client, store, _ = board
    assert client.post(PREFIX, json=PROVIDER).status_code == 403
    assert store.list_providers() == []


MUTATING_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", PREFIX, PROVIDER),
    ("PUT", f"{PREFIX}/made-up", {}),
    ("DELETE", f"{PREFIX}/made-up", None),
    ("PUT", f"{PREFIX}/made-up/key", {"key": SECRET}),
    ("DELETE", f"{PREFIX}/made-up/key", None),
    ("POST", f"{PREFIX}/made-up/test", {}),
    ("POST", f"{PREFIX}/made-up/probe", {}),
    ("POST", f"{PREFIX}/made-up/models", {}),
    ("PUT", f"{PREFIX}/made-up/models/some-model", {}),
    ("DELETE", f"{PREFIX}/made-up/models/some-model", None),
]


@pytest.mark.parametrize(("method", "url", "body"), MUTATING_ROUTES)
def test_every_mutating_provider_route_is_refused_without_the_header(
    board, method: str, url: str, body: dict[str, Any] | None
) -> None:
    client, store, secrets = board
    response = client.request(method, url, json=body)
    assert response.status_code == 403
    assert store.list_providers() == []
    assert secrets.key_state("made-up").has_key is False


def test_templates_lists_templates_without_any_key(board) -> None:
    client, _, _ = board
    response = client.get(f"{PREFIX}/templates")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"templates"}
    assert len(body["templates"]) > 0
    for item in body["templates"]:
        assert set(item) == {"name", "base_url", "api_type", "hint", "verified"}
        assert isinstance(item["verified"], bool)
    assert _key_fields(body) == []


def test_put_requires_expected_version(board) -> None:
    client, _, _ = board
    _create(client)
    response = client.put(f"{PREFIX}/made-up", json={"label": "New"}, headers=HEADERS)
    assert response.status_code == 400
    assert "expected_version" in response.json()["error"]["message"]


def test_put_with_a_stale_version_conflicts(board) -> None:
    client, _, _ = board
    _create(client)
    stale = _version(client)
    first = client.put(
        f"{PREFIX}/made-up", json={"label": "New", "expected_version": stale}, headers=HEADERS
    )
    assert first.status_code == 200
    assert first.json()["provider"]["label"] == "New"
    second = client.put(
        f"{PREFIX}/made-up", json={"label": "Newer", "expected_version": stale}, headers=HEADERS
    )
    assert second.status_code == 409


def test_put_dry_run_returns_the_diff_and_writes_nothing(board) -> None:
    client, _, _ = board
    _create(client)
    body = {"label": "New", "expected_version": _version(client), "dry_run": True}
    response = client.put(f"{PREFIX}/made-up", json=body, headers=HEADERS).json()
    assert response["dry_run"] is True
    assert response["diff"] == {"label": {"before": "Made up", "after": "New"}}
    assert client.get(f"{PREFIX}/made-up").json()["provider"]["label"] == "Made up"


def test_put_refuses_name_and_unknown_fields(board) -> None:
    client, _, _ = board
    _create(client)
    body = {"name": "other", "expected_version": _version(client)}
    assert client.put(f"{PREFIX}/made-up", json=body, headers=HEADERS).status_code == 400


def test_delete_is_refused_while_a_mission_role_uses_the_provider(board) -> None:
    client, store, secrets = board
    _create(client)
    secrets.set_key("made-up", SECRET)
    _seed_mission(store, {"worker": "made-up/model-a"})
    refused = client.delete(
        f"{PREFIX}/made-up", params={"expected_version": _version(client)}, headers=HEADERS
    )
    assert refused.status_code == 409
    assert "m1" in refused.json()["error"]["message"]
    assert secrets.key_state("made-up").has_key is True


def test_delete_proceeds_once_no_role_uses_the_provider(board) -> None:
    client, store, secrets = board
    _create(client)
    secrets.set_key("made-up", SECRET)
    _seed_mission(store, {"worker": "anthropic/sonnet"})
    response = client.delete(
        f"{PREFIX}/made-up", params={"expected_version": _version(client)}, headers=HEADERS
    )
    assert response.status_code == 200
    assert client.get(f"{PREFIX}/made-up").status_code == 404
    assert secrets.key_state("made-up").has_key is False


def test_delete_with_a_stale_version_conflicts(board) -> None:
    client, _, _ = board
    _create(client)
    response = client.delete(
        f"{PREFIX}/made-up", params={"expected_version": "2000-01-01T00:00:00+00:00"},
        headers=HEADERS,
    )
    assert response.status_code == 409


def test_model_add_update_and_remove(board) -> None:
    client, _, _ = board
    _create(client)
    added = client.post(
        f"{PREFIX}/made-up/models",
        json={"model": "qwen-worker", "price_input_per_mtok_usd": 0.5,
              "capability_overrides": {"tools": False}},
        headers=HEADERS,
    )
    assert added.status_code == 200
    assert added.json()["provider"]["models"][0]["model"] == "qwen-worker"
    assert client.post(
        f"{PREFIX}/made-up/models", json={"model": "qwen-worker"}, headers=HEADERS
    ).status_code == 409

    stale = _model_version(client, "qwen-worker")
    updated = client.put(
        f"{PREFIX}/made-up/models/qwen-worker",
        json={"expected_version": stale, "alias": "qw"}, headers=HEADERS,
    )
    assert updated.status_code == 200
    assert updated.json()["provider"]["models"][0]["alias"] == "qw"
    assert client.put(
        f"{PREFIX}/made-up/models/qwen-worker",
        json={"expected_version": stale, "alias": "again"}, headers=HEADERS,
    ).status_code == 409

    current = _model_version(client, "qwen-worker")
    removed = client.delete(
        f"{PREFIX}/made-up/models/qwen-worker",
        params={"expected_version": current}, headers=HEADERS,
    )
    assert removed.status_code == 200
    assert removed.json()["provider"]["models"] == []
    assert client.delete(
        f"{PREFIX}/made-up/models/qwen-worker",
        params={"expected_version": current}, headers=HEADERS,
    ).status_code == 404


def test_model_name_with_a_slash_round_trips(board) -> None:
    client, _, _ = board
    _create(client)
    client.post(
        f"{PREFIX}/made-up/models", json={"model": "meta-llama/Llama-3"}, headers=HEADERS
    )
    version = _model_version(client, "meta-llama/Llama-3")
    response = client.put(
        f"{PREFIX}/made-up/models/meta-llama/Llama-3",
        json={"expected_version": version, "notes_alias": "x", "alias": "llama"},
        headers=HEADERS,
    )
    assert response.status_code == 400
    response = client.put(
        f"{PREFIX}/made-up/models/meta-llama/Llama-3",
        json={"expected_version": version, "alias": "llama"}, headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["provider"]["models"][0]["alias"] == "llama"


@pytest.mark.parametrize(
    "body",
    [
        {"model": "bad model!"},
        {"model": "ok", "api_type": "no-such-api"},
        {"model": "ok", "capability_overrides": {"bogus": 1}},
        {"model": "ok", "price_output_per_mtok_usd": -1},
        {"model": "ok", "probe_result": {"ok": True}},
        {"model": "ok", "supports_thinking": None},
    ],
)
def test_model_validation_refuses_the_body(board, body: dict[str, Any]) -> None:
    client, store, _ = board
    _create(client)
    assert client.post(f"{PREFIX}/made-up/models", json=body, headers=HEADERS).status_code == 400
    assert store.get_provider("made-up")["models"] == []


def test_model_under_a_missing_provider_is_not_found(board) -> None:
    client, _, _ = board
    response = client.post(f"{PREFIX}/nope/models", json={"model": "m"}, headers=HEADERS)
    assert response.status_code == 404


def _add_model(client: TestClient, model: str = "qwen-worker") -> None:
    response = client.post(f"{PREFIX}/made-up/models", json={"model": model}, headers=HEADERS)
    assert response.status_code == 200


def _put_key(client: TestClient, key: str = SECRET) -> Any:
    return client.put(f"{PREFIX}/made-up/key", json={"key": key}, headers=HEADERS)


def test_put_key_stores_it_and_returns_only_key_state(board, caplog) -> None:
    client, store, secrets = board
    _create(client)
    caplog.set_level(logging.DEBUG)
    response = _put_key(client)
    assert response.status_code == 200
    assert set(response.json()) == {"key_state"}
    assert response.json()["key_state"]["has_key"] is True
    assert response.json()["key_state"]["set_at"]
    assert secrets.get_key("made-up") == SECRET
    assert SECRET not in response.text
    assert SECRET not in caplog.text
    assert SECRET not in str(store.changelog())


def test_put_key_without_the_dashboard_header_is_refused(board) -> None:
    client, _, secrets = board
    _create(client)
    assert client.put(f"{PREFIX}/made-up/key", json={"key": SECRET}).status_code == 403
    assert secrets.key_state("made-up").has_key is False


@pytest.mark.parametrize(
    "body", [{"key": ""}, {"key": "   "}, {"key": 5}, {"key": SECRET, "label": "x"}]
)
def test_put_key_refuses_a_bad_body_without_echoing_it(board, body: dict[str, Any]) -> None:
    client, _, secrets = board
    _create(client)
    response = client.put(f"{PREFIX}/made-up/key", json=body, headers=HEADERS)
    assert response.status_code == 400
    assert SECRET not in response.text
    assert secrets.key_state("made-up").has_key is False


def test_delete_key_clears_the_key_and_keeps_the_provider(board, caplog) -> None:
    client, _, secrets = board
    _create(client)
    _put_key(client)
    caplog.set_level(logging.DEBUG)
    response = client.delete(f"{PREFIX}/made-up/key", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"key_state": {"has_key": False, "set_at": None}}
    assert secrets.key_state("made-up").has_key is False
    assert client.get(f"{PREFIX}/made-up").status_code == 200
    assert SECRET not in response.text + caplog.text


def test_key_routes_for_a_missing_provider_are_not_found(board) -> None:
    client, _, _ = board
    assert _put_key(client).status_code == 404
    assert client.delete(f"{PREFIX}/made-up/key", headers=HEADERS).status_code == 404


def test_test_sends_one_request_with_one_max_token_and_returns_status_and_latency(
    board, fake_post: FakePost, caplog
) -> None:
    client, store, _ = board
    _create(client)
    _put_key(client)
    _add_model(client)
    caplog.set_level(logging.DEBUG)
    response = client.post(
        f"{PREFIX}/made-up/test", json={"model": "qwen-worker"}, headers=HEADERS
    )
    assert response.status_code == 200
    assert set(response.json()) == {"status", "latency_ms"}
    assert response.json()["status"] == 200
    assert isinstance(response.json()["latency_ms"], int)
    assert len(fake_post.calls) == 1
    url, headers, body, _ = fake_post.calls[0]
    assert url == "https://made-up.example/v1/chat/completions"
    assert headers == {"Authorization": f"Bearer {SECRET}"}
    assert body["model"] == "qwen-worker"
    assert body["max_tokens"] == 1
    assert SECRET not in response.text + caplog.text + str(store.changelog())


def test_test_reports_no_status_on_a_transport_error_and_leaks_nothing(
    board, fake_post: FakePost, caplog
) -> None:
    client, store, _ = board
    _create(client)
    _put_key(client)
    _add_model(client)
    fake_post.error = httpx.ConnectError(f"refused, key {SECRET}")
    caplog.set_level(logging.DEBUG)
    response = client.post(
        f"{PREFIX}/made-up/test", json={"model": "qwen-worker"}, headers=HEADERS
    )
    assert response.json()["status"] is None
    assert "ConnectError" in caplog.text
    assert SECRET not in response.text + caplog.text + str(store.changelog())


def test_test_without_a_key_is_a_conflict_and_sends_nothing(board, fake_post: FakePost) -> None:
    client, _, _ = board
    _create(client)
    _add_model(client)
    response = client.post(
        f"{PREFIX}/made-up/test", json={"model": "qwen-worker"}, headers=HEADERS
    )
    assert response.status_code == 409
    assert fake_post.calls == []


def test_test_for_an_unknown_model_is_not_found_and_sends_nothing(
    board, fake_post: FakePost
) -> None:
    client, _, _ = board
    _create(client)
    _put_key(client)
    response = client.post(f"{PREFIX}/made-up/test", json={"model": "nope"}, headers=HEADERS)
    assert response.status_code == 404
    assert fake_post.calls == []


def test_probe_that_fails_tools_sets_the_worker_warning(
    board, fake_post: FakePost, caplog
) -> None:
    client, store, _ = board
    _create(client)
    _put_key(client)
    _add_model(client)
    fake_post.status_for = lambda body: 400 if "tools" in body else 200
    caplog.set_level(logging.DEBUG)
    response = client.post(
        f"{PREFIX}/made-up/probe", json={"model": "qwen-worker"}, headers=HEADERS
    )
    assert response.status_code == 200
    expected = {"tools": False, "streaming": True, "system": True, "worker_warning": "no_tools"}
    assert response.json()["probe"] == expected
    stored = response.json()["provider"]["models"][0]
    assert stored["probe_result"] == expected
    assert stored["probed_at"]
    assert len(fake_post.calls) == 3
    assert all(body["max_tokens"] == 16 for _, _, body, _ in fake_post.calls)
    assert SECRET not in response.text + caplog.text + str(store.changelog())


def test_probe_that_passes_sets_no_warning(board, fake_post: FakePost) -> None:
    client, _, _ = board
    _create(client)
    _put_key(client)
    _add_model(client)
    response = client.post(
        f"{PREFIX}/made-up/probe", json={"model": "qwen-worker"}, headers=HEADERS
    )
    expected = {"tools": True, "streaming": True, "system": True, "worker_warning": None}
    assert response.json()["probe"] == expected


def test_import_from_env_proposes_rows_without_any_secret_value(
    tmp_path: Path, fake_post: FakePost, caplog
) -> None:
    profile = ProviderProfile(
        name="LOCAL",
        env={
            "ANTHROPIC_BASE_URL": "https://made-up.example/anthropic",
            "ANTHROPIC_AUTH_TOKEN": SECRET,
        },
    )
    client, store, secrets = _make_board(tmp_path, fake_post, {"LOCAL": profile})
    caplog.set_level(logging.DEBUG)
    response = client.get(f"{PREFIX}/import-from-env")
    assert response.status_code == 200
    assert response.json() == {
        "dry_run": True,
        "proposals": [
            {
                "name": "local",
                "has_key": True,
                "exists": False,
                "fields": {
                    "label": "local",
                    "api_type": "anthropic",
                    "base_url": "https://made-up.example/anthropic",
                },
            }
        ],
    }
    assert SECRET not in response.text + caplog.text
    assert store.list_providers() == []
    assert secrets.key_state("local").has_key is False


def test_import_from_env_marks_an_existing_provider_and_reports_a_bad_base_url(
    tmp_path: Path, fake_post: FakePost
) -> None:
    providers = {
        "MADE_UP": ProviderProfile(
            name="MADE_UP", env={"ANTHROPIC_BASE_URL": "https://made-up.example/v1"}
        ),
        "BROKEN": ProviderProfile(
            name="BROKEN", env={"ANTHROPIC_BASE_URL": "ftp://user:hunter2@broken.example"}
        ),
    }
    client, _, _ = _make_board(tmp_path, fake_post, providers)
    _create(client)
    response = client.get(f"{PREFIX}/import-from-env")
    rows = {row["name"]: row for row in response.json()["proposals"]}
    assert rows["made-up"]["exists"] is True
    assert rows["made-up"]["has_key"] is False
    assert "error" in rows["broken"]
    assert "hunter2" not in response.text


def test_no_route_response_or_changelog_carries_the_secret(
    board, fake_post: FakePost, caplog
) -> None:
    client, store, _ = board
    caplog.set_level(logging.DEBUG)
    _create(client)
    _add_model(client)
    responses = [
        _put_key(client),
        client.get(PREFIX),
        client.get(f"{PREFIX}/made-up"),
        client.get(f"{PREFIX}/import-from-env"),
        client.post(f"{PREFIX}/made-up/test", json={"model": "qwen-worker"}, headers=HEADERS),
        client.post(f"{PREFIX}/made-up/probe", json={"model": "qwen-worker"}, headers=HEADERS),
        client.delete(f"{PREFIX}/made-up/key", headers=HEADERS),
    ]
    assert all(SECRET not in response.text for response in responses)
    assert SECRET not in caplog.text
    assert SECRET not in str(store.changelog())
