"""T020 — the global ``roles_json`` (hermes_config) and the mission override
(mission_config key ``roles_json``) through the guarded save path.  The
in-memory store only, no database."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from claude_runner.api import create_app
from claude_runner.board import InMemoryBoardStore, Mission
from claude_runner.config import ProviderProfile, Settings

HEADER = {"X-AHAWR-Dashboard": "1"}
VALID = '{"worker": {"1": "anthropic/haiku", "2": "local/qwen"}}'
UNKNOWN_PROVIDER = '{"worker": {"1": "nowhere/model"}}'
STALE = "2000-01-01T00:00:00+00:00"


@pytest.fixture
async def roles_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    store = InMemoryBoardStore()
    store._missions["m1"] = Mission(
        mission_id="m1", state_namespace="sk1", name="Source",
        working_directory="/tmp/x", updated_at="2026-10-17T00:00:00+00:00",
    )
    providers_settings = dataclasses.replace(settings, providers={
        "anthropic": ProviderProfile(name="anthropic"),
        "local": ProviderProfile(name="local"),
    })
    app = create_app(providers_settings)
    app.state.board_store = store
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _version(client: AsyncClient, path: str) -> Any:
    body: dict[str, Any] = (await client.get(f"/api/board{path}")).json()
    return body["version"]


async def _global_roles(client: AsyncClient) -> str:
    return str((await client.get("/api/board/roles")).json()["roles_json"])


async def test_get_global_roles_starts_empty(roles_client: AsyncClient) -> None:
    r = await roles_client.get("/api/board/roles")
    assert r.status_code == 200
    assert r.json()["roles_json"] == ""


async def test_put_global_roles_without_header_is_403(roles_client: AsyncClient) -> None:
    r = await roles_client.put("/api/board/roles", json={"roles_json": VALID})
    assert r.status_code == 403
    assert await _global_roles(roles_client) == ""


async def test_put_global_roles_stale_version_is_409(roles_client: AsyncClient) -> None:
    first = await roles_client.put("/api/board/roles", headers=HEADER,
                                   json={"roles_json": VALID})
    assert first.status_code == 200
    stale = await roles_client.put("/api/board/roles", headers=HEADER, json={
        "roles_json": '{"worker": "anthropic/sonnet"}', "expected_version": STALE,
    })
    assert stale.status_code == 409
    assert await _global_roles(roles_client) == VALID


async def test_put_global_roles_unknown_provider_is_400_naming_the_role(
    roles_client: AsyncClient,
) -> None:
    r = await roles_client.put("/api/board/roles", headers=HEADER,
                               json={"roles_json": UNKNOWN_PROVIDER})
    assert r.status_code == 400
    message = r.json()["error"]["message"]
    assert "role 'worker'" in message and "unknown provider 'nowhere'" in message
    assert await _global_roles(roles_client) == ""


async def test_put_global_roles_invalid_json_is_400(roles_client: AsyncClient) -> None:
    r = await roles_client.put("/api/board/roles", headers=HEADER,
                               json={"roles_json": '{"worker": '})
    assert r.status_code == 400
    assert "not valid JSON" in r.json()["error"]["message"]


def _worker_text(options: dict[str, Any]) -> str:
    return json.dumps({"worker": {"1": "anthropic/haiku", "options": options}})


POLLING_OPTIONS = {"max_polls": 40, "poll_seconds": 2.5, "max_retries": 0}


async def test_put_global_roles_valid_polling_options_are_saved(roles_client: AsyncClient) -> None:
    text = _worker_text(POLLING_OPTIONS)
    r = await roles_client.put("/api/board/roles", headers=HEADER, json={"roles_json": text})
    assert r.status_code == 200
    assert await _global_roles(roles_client) == text


async def test_put_global_roles_absent_options_are_saved(roles_client: AsyncClient) -> None:
    r = await roles_client.put("/api/board/roles", headers=HEADER, json={"roles_json": VALID})
    assert r.status_code == 200
    assert await _global_roles(roles_client) == VALID


@pytest.mark.parametrize(("options", "reason"), [
    ({"max_polls": 0}, "option 'max_polls' must be a whole number of at least 1"),
    ({"max_polls": "3"}, "option 'max_polls' must be a whole number of at least 1"),
    ({"max_retries": -1}, "option 'max_retries' must be a whole number of at least 0"),
    ({"poll_seconds": 0}, "option 'poll_seconds' must be a positive number"),
    ({"poll_seconds": True}, "option 'poll_seconds' must be a positive number"),
    ({"max_poll": 3}, "unknown option 'max_poll'"),
])
async def test_put_global_roles_invalid_polling_option_is_400_with_reason(
    roles_client: AsyncClient, options: dict[str, Any], reason: str,
) -> None:
    r = await roles_client.put("/api/board/roles", headers=HEADER,
                               json={"roles_json": _worker_text(options)})
    assert r.status_code == 400
    message = r.json()["error"]["message"]
    assert "role 'worker'" in message and reason in message
    assert await _global_roles(roles_client) == ""


async def test_put_mission_roles_polling_options_stale_version_is_409(
    roles_client: AsyncClient,
) -> None:
    saved = await roles_client.put("/api/board/missions/m1/roles", headers=HEADER,
                                   json={"roles_json": VALID})
    assert saved.status_code == 200
    stale = await roles_client.put("/api/board/missions/m1/roles", headers=HEADER, json={
        "roles_json": _worker_text(POLLING_OPTIONS), "expected_version": STALE,
    })
    assert stale.status_code == 409
    got = await roles_client.get("/api/board/missions/m1/roles")
    assert got.json()["roles_json"] == VALID


async def test_put_global_roles_valid_save_writes_history(roles_client: AsyncClient) -> None:
    version = await _version(roles_client, "/roles")
    r = await roles_client.put("/api/board/roles", headers=HEADER, json={
        "roles_json": VALID, "expected_version": version,
    })
    assert r.status_code == 200
    assert await _global_roles(roles_client) == VALID
    log: list[dict[str, Any]] = (await roles_client.get("/api/board/changelog")).json()["changelog"]
    assert "hermes_config update" in [entry["action"] for entry in log]


async def test_put_global_roles_empty_clears_the_value(roles_client: AsyncClient) -> None:
    saved = await roles_client.put("/api/board/roles", headers=HEADER,
                                  json={"roles_json": VALID})
    assert saved.status_code == 200
    r = await roles_client.put("/api/board/roles", headers=HEADER,
                              json={"roles_json": ""})
    assert r.status_code == 200
    assert await _global_roles(roles_client) == ""


async def test_put_mission_roles_without_header_is_403(roles_client: AsyncClient) -> None:
    r = await roles_client.put("/api/board/missions/m1/roles", json={"roles_json": VALID})
    assert r.status_code == 403


async def test_put_mission_roles_stale_version_is_409(roles_client: AsyncClient) -> None:
    await roles_client.put("/api/board/missions/m1/roles", headers=HEADER,
                           json={"roles_json": VALID})
    r = await roles_client.put("/api/board/missions/m1/roles", headers=HEADER, json={
        "roles_json": VALID, "expected_version": STALE,
    })
    assert r.status_code == 409


async def test_put_mission_roles_invalid_is_400_and_unchanged(roles_client: AsyncClient) -> None:
    await roles_client.put("/api/board/missions/m1/roles", headers=HEADER,
                           json={"roles_json": VALID})
    version = await _version(roles_client, "/missions/m1/roles")
    r = await roles_client.put("/api/board/missions/m1/roles", headers=HEADER,
                               json={"roles_json": UNKNOWN_PROVIDER, "expected_version": version})
    assert r.status_code == 400
    assert "role 'worker'" in r.json()["error"]["message"]
    got = await roles_client.get("/api/board/missions/m1/roles")
    assert got.json()["roles_json"] == VALID


async def test_mission_roles_valid_save_round_trips(roles_client: AsyncClient) -> None:
    version = await _version(roles_client, "/missions/m1/roles")
    r = await roles_client.put("/api/board/missions/m1/roles", headers=HEADER, json={
        "roles_json": VALID, "expected_version": version,
    })
    assert r.status_code == 200
    got = await roles_client.get("/api/board/missions/m1/roles")
    assert got.json()["roles_json"] == VALID
    assert got.json()["version"] == r.json()["version"]


async def test_put_mission_roles_empty_clears_the_value(roles_client: AsyncClient) -> None:
    saved = await roles_client.put("/api/board/missions/m1/roles", headers=HEADER,
                                  json={"roles_json": VALID})
    assert saved.status_code == 200
    r = await roles_client.put("/api/board/missions/m1/roles", headers=HEADER,
                              json={"roles_json": ""})
    assert r.status_code == 200
    got = await roles_client.get("/api/board/missions/m1/roles")
    assert got.json()["roles_json"] == ""


async def test_mission_roles_unknown_mission_is_404(roles_client: AsyncClient) -> None:
    r = await roles_client.get("/api/board/missions/nope/roles")
    assert r.status_code == 404


ENGINE = '{"max_attempts_per_task": 3, "runner_url": "http://runner-a:8700/"}'


async def test_put_engine_without_header_is_403(roles_client: AsyncClient) -> None:
    r = await roles_client.put("/api/board/engine", json={"engine_json": ENGINE})
    assert r.status_code == 403
    assert (await roles_client.get("/api/board/engine")).json()["engine_json"] == ""


async def test_put_engine_valid_save_at_current_version(roles_client: AsyncClient) -> None:
    before = await _version(roles_client, "/engine")
    r = await roles_client.put("/api/board/engine", headers=HEADER,
                               json={"engine_json": ENGINE, "expected_version": before})
    assert r.status_code == 200
    got = (await roles_client.get("/api/board/engine")).json()
    assert got["engine_json"] == ENGINE
    assert got["version"] != before
    assert r.json()["version"] == got["version"]


async def test_put_engine_stale_version_is_409(roles_client: AsyncClient) -> None:
    saved = await roles_client.put("/api/board/engine", headers=HEADER,
                                   json={"engine_json": ENGINE})
    assert saved.status_code == 200
    stale = await roles_client.put("/api/board/engine", headers=HEADER, json={
        "engine_json": '{"max_attempts_per_task": 9}', "expected_version": STALE,
    })
    assert stale.status_code == 409
    assert (await roles_client.get("/api/board/engine")).json()["engine_json"] == ENGINE


@pytest.mark.parametrize(("text", "reason"), [
    ('{"max_attempts_per_task": "x"}', "max_attempts_per_task must be a number"),
    ('{"max_attempts_per_task": 3', "is not valid JSON"),
])
async def test_put_engine_invalid_value_is_rejected_and_not_stored(
    roles_client: AsyncClient, text: str, reason: str,
) -> None:
    r = await roles_client.put("/api/board/engine", headers=HEADER,
                               json={"engine_json": text})
    assert r.status_code == 400
    assert reason in r.text
    assert (await roles_client.get("/api/board/engine")).json()["engine_json"] == ""
