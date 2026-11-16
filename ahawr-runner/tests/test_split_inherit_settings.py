"""A part of a split takes the settings of its parent (operator 2026-11-14).

The Create Part Missions node sends ``inherit_settings_from`` with the part's fields. The create
route copies the parent's ``overrides_json`` (roles, features, retrieval), its config row and the
attempts of its queue entry onto the new mission; without the key nothing is copied.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient

from ahawr_runner import board
from ahawr_runner.config import Settings
from tests.test_mission_dependencies import (
    FLAG_OFF_JSON,
    PINNING_STAMP,
    DependencyStore,
    client_for_store,
)

ROLES_JSON = json.dumps(
    {
        "roles": {
            "worker": {"1": "anthropic/claude-sonnet-5-5"},
            "reviewer": {"1": "anthropic/claude-fable-5-2"},
        }
    }
)
PART_BODY = {
    "mission_id": "m1-p2",
    "name": "Original (part 2 of 2)",
    "plan": {"tasks": [{"id": "T2", "objective": "second part"}]},
    "who": "adaptive_split",
}


def parent_store() -> DependencyStore:
    """A store with the split mission m1: roles override, a config row and 5 attempts per task."""
    store = DependencyStore()
    store._missions["m1"] = board.Mission(
        mission_id="m1",
        state_namespace="sk1",
        name="Original",
        working_directory="/tmp/x",
        overrides_json=ROLES_JSON,
        updated_at=PINNING_STAMP,
    )
    store._queue["m1"] = board.QueueEntry(
        position=0, mission_id="m1", enabled=True, attempts_per_task=5
    )
    store._queue_order.append("m1")
    store._mission_config["m1"] = {"config_json": {"hint": "keep"}, "updated_at": PINNING_STAMP}
    plan = {"tasks": [{"id": "T1", "objective": "one"}]}
    store.upsert_live_state("sk1", None, plan=plan, status="active")
    store.update_hermes_config(None, features_json=FLAG_OFF_JSON)
    return store


@pytest.fixture
def store() -> DependencyStore:
    return parent_store()


@pytest.fixture
async def client(settings: Settings, store: DependencyStore) -> AsyncGenerator[AsyncClient, None]:
    async for api_client in client_for_store(settings, store):
        yield api_client


async def test_split_part_takes_roles_config_and_attempts_from_the_parent(
    client: AsyncClient, store: DependencyStore
) -> None:
    answer = await client.post(
        "/api/board/missions/create", json={**PART_BODY, "inherit_settings_from": "m1"}
    )
    assert answer.status_code == 200, answer.text
    part = store.get_mission("m1-p2")
    assert part is not None
    assert part.overrides_json == ROLES_JSON
    assert store.mission_config("m1-p2") == {"hint": "keep"}
    entry = next(e for e in store.queue() if e.mission_id == "m1-p2")
    assert entry.attempts_per_task == 5


async def test_split_part_without_the_key_takes_nothing(
    client: AsyncClient, store: DependencyStore
) -> None:
    answer = await client.post("/api/board/missions/create", json=PART_BODY)
    assert answer.status_code == 200, answer.text
    part = store.get_mission("m1-p2")
    assert part is not None
    assert part.overrides_json is None
    assert not store.mission_config("m1-p2")
    entry = next(e for e in store.queue() if e.mission_id == "m1-p2")
    assert entry.attempts_per_task is None


async def test_split_part_naming_a_missing_parent_is_refused_and_creates_nothing(
    client: AsyncClient, store: DependencyStore
) -> None:
    answer = await client.post(
        "/api/board/missions/create", json={**PART_BODY, "inherit_settings_from": "ghost"}
    )
    assert answer.status_code == 404
    assert store.get_mission("m1-p2") is None
