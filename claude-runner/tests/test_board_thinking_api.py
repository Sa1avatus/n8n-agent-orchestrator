"""The global ``thinking_json`` (hermes_config) through the guarded GET/PUT /api/board/thinking.
The in-memory store only, no database."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from claude_runner.api import create_app
from claude_runner.board import InMemoryBoardStore
from claude_runner.config import ProviderProfile, Settings

HEADER = {"X-AHAWR-Dashboard": "1"}
VALID = '{"from_task": 2, "off_after_seconds": 1800}'
UNKNOWN_KEY = '{"bogus": 1}'
STALE = "2000-01-01T00:00:00+00:00"


@pytest.fixture
async def thinking_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    store = InMemoryBoardStore()
    providers_settings = dataclasses.replace(settings, providers={
        "anthropic": ProviderProfile(name="anthropic"),
    })
    app = create_app(providers_settings)
    app.state.board_store = store
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _version(client: AsyncClient) -> Any:
    body: dict[str, Any] = (await client.get("/api/board/thinking")).json()
    return body["version"]


async def test_get_thinking_starts_empty(thinking_client: AsyncClient) -> None:
    r = await thinking_client.get("/api/board/thinking")
    assert r.status_code == 200
    assert r.json()["thinking_json"] == ""


async def test_put_thinking_without_header_is_403(thinking_client: AsyncClient) -> None:
    r = await thinking_client.put("/api/board/thinking", json={"thinking_json": VALID})
    assert r.status_code == 403


async def test_put_thinking_valid_is_saved(thinking_client: AsyncClient) -> None:
    r = await thinking_client.put("/api/board/thinking", headers=HEADER,
                                  json={"thinking_json": VALID})
    assert r.status_code == 200
    got = await thinking_client.get("/api/board/thinking")
    assert got.json()["thinking_json"] == VALID


async def test_put_thinking_unknown_key_is_400_and_unchanged(thinking_client: AsyncClient) -> None:
    await thinking_client.put("/api/board/thinking", headers=HEADER,
                              json={"thinking_json": VALID})
    version = await _version(thinking_client)
    r = await thinking_client.put("/api/board/thinking", headers=HEADER,
                                  json={"thinking_json": UNKNOWN_KEY, "expected_version": version})
    assert r.status_code == 400
    assert 'unknown key "bogus"' in r.json()["error"]["message"]
    got = await thinking_client.get("/api/board/thinking")
    assert got.json()["thinking_json"] == VALID


async def test_put_thinking_stale_version_is_409(thinking_client: AsyncClient) -> None:
    await thinking_client.put("/api/board/thinking", headers=HEADER,
                              json={"thinking_json": VALID})
    r = await thinking_client.put("/api/board/thinking", headers=HEADER, json={
        "thinking_json": VALID, "expected_version": STALE,
    })
    assert r.status_code == 409
