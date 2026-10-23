"""The global ``features_json`` (hermes_config) and the mission override
(mission_config key ``mission_features_json``) through the guarded save path.
The in-memory store only, no database."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from claude_runner.api import create_app
from claude_runner.board import InMemoryBoardStore, Mission
from claude_runner.config import ProviderProfile, Settings

HEADER = {"X-AHAWR-Dashboard": "1"}
VALID = '{"plan_no_truncate": {"enabled": true, "max_tasks": 10, "max_tasks_hard": 20}}'
UNKNOWN_FEATURE = '{"nope": {"enabled": true}}'
NOT_BOOLEAN = '{"run_hygiene": {"enabled": "yes"}}'
HARD_BELOW_SOFT = '{"plan_no_truncate": {"max_tasks_hard": 5}}'
STALE = "2000-01-01T00:00:00+00:00"


@pytest.fixture
async def features_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    store = InMemoryBoardStore()
    store._missions["m1"] = Mission(
        mission_id="m1", state_namespace="sk1", name="Source",
        working_directory="/tmp/x", updated_at="2026-10-17T00:00:00+00:00",
    )
    providers_settings = dataclasses.replace(settings, providers={
        "anthropic": ProviderProfile(name="anthropic"),
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


async def test_get_global_features_starts_empty(features_client: AsyncClient) -> None:
    r = await features_client.get("/api/board/features")
    assert r.status_code == 200
    assert r.json()["features_json"] == ""


async def test_get_mission_features_starts_empty(features_client: AsyncClient) -> None:
    r = await features_client.get("/api/board/missions/m1/features")
    assert r.status_code == 200
    assert r.json()["mission_features_json"] == ""


async def test_put_global_features_without_header_is_403(features_client: AsyncClient) -> None:
    r = await features_client.put("/api/board/features", json={"features_json": VALID})
    assert r.status_code == 403


async def test_put_mission_features_without_header_is_403(features_client: AsyncClient) -> None:
    r = await features_client.put(
        "/api/board/missions/m1/features", json={"mission_features_json": VALID},
    )
    assert r.status_code == 403


async def test_put_global_features_valid_is_saved(features_client: AsyncClient) -> None:
    r = await features_client.put("/api/board/features", headers=HEADER,
                                  json={"features_json": VALID})
    assert r.status_code == 200
    got = await features_client.get("/api/board/features")
    assert got.json()["features_json"] == VALID


@pytest.mark.parametrize(("value", "reason"), [
    (UNKNOWN_FEATURE, 'unknown feature "nope"'),
    (NOT_BOOLEAN, "run_hygiene.enabled must be true or false"),
])
async def test_put_global_features_invalid_is_400_and_unchanged(
    features_client: AsyncClient, value: str, reason: str,
) -> None:
    await features_client.put("/api/board/features", headers=HEADER,
                              json={"features_json": VALID})
    version = await _version(features_client, "/features")
    r = await features_client.put("/api/board/features", headers=HEADER,
                                  json={"features_json": value, "expected_version": version})
    assert r.status_code == 400
    assert reason in r.json()["error"]["message"]
    got = await features_client.get("/api/board/features")
    assert got.json()["features_json"] == VALID


async def test_put_global_features_stale_version_is_409(features_client: AsyncClient) -> None:
    await features_client.put("/api/board/features", headers=HEADER,
                              json={"features_json": VALID})
    r = await features_client.put("/api/board/features", headers=HEADER, json={
        "features_json": VALID, "expected_version": STALE,
    })
    assert r.status_code == 409


async def test_put_global_features_non_string_is_400(features_client: AsyncClient) -> None:
    r = await features_client.put("/api/board/features", headers=HEADER,
                                  json={"features_json": {"run_hygiene": {}}})
    assert r.status_code == 400
    assert "features_json must be a string" in r.json()["error"]["message"]


async def test_put_mission_features_valid_is_saved(features_client: AsyncClient) -> None:
    r = await features_client.put("/api/board/missions/m1/features", headers=HEADER,
                                  json={"mission_features_json": VALID})
    assert r.status_code == 200
    got = await features_client.get("/api/board/missions/m1/features")
    assert got.json()["mission_features_json"] == VALID


async def test_put_mission_features_invalid_is_400_and_unchanged(
    features_client: AsyncClient,
) -> None:
    await features_client.put("/api/board/missions/m1/features", headers=HEADER,
                              json={"mission_features_json": VALID})
    version = await _version(features_client, "/missions/m1/features")
    r = await features_client.put("/api/board/missions/m1/features", headers=HEADER, json={
        "mission_features_json": UNKNOWN_FEATURE, "expected_version": version,
    })
    assert r.status_code == 400
    assert 'unknown feature "nope"' in r.json()["error"]["message"]
    got = await features_client.get("/api/board/missions/m1/features")
    assert got.json()["mission_features_json"] == VALID


async def test_put_mission_features_checked_against_global(
    features_client: AsyncClient,
) -> None:
    r = await features_client.put("/api/board/missions/m1/features", headers=HEADER,
                                  json={"mission_features_json": HARD_BELOW_SOFT})
    assert r.status_code == 400
    assert "max_tasks_hard must be at least plan_no_truncate.max_tasks (15)" in (
        r.json()["error"]["message"]
    )


async def test_put_mission_features_unknown_mission_is_404(features_client: AsyncClient) -> None:
    r = await features_client.put("/api/board/missions/nope/features", headers=HEADER,
                                  json={"mission_features_json": VALID})
    assert r.status_code == 404
