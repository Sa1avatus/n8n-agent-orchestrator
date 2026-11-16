"""Tests for data-tables/ops/split_dependencies_patch.py (T030): the adaptive
split parts get ``depends_on``.

Part N (N >= 2) waits for part N-1; part 1 has no previous part, so its slice
is empty.  The patch is idempotent and refuses a missing or doubled anchor with
the anchor named.  The create route stores a sent ``depends_on`` with the
``mission_dependencies`` flag on and drops it with the flag off; a split part
stored without the field reads an empty list and classifies as ``due``.

The export case applies the patch to a temporary copy of the launch export
(`data-tables/ops/wf_next.json`); when that file is not in the environment the
test skips with the reason and the fixture cases still cover the same behaviour.
Nothing here writes to the live export file.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from httpx import AsyncClient

import ahawr_runner.board_dependencies as BD
from ahawr_runner import board
from ahawr_runner.config import Settings
from tests.test_mission_dependencies import (
    FLAG_OFF_JSON,
    FLAG_ON_JSON,
    PINNING_STAMP,
    DependencyStore,
    client_for_store,
)

OPS_DIR = Path(__file__).resolve().parents[2] / "data-tables" / "ops"
PATCH_PATH = OPS_DIR / "split_dependencies_patch.py"
EXPORT_COPY = OPS_DIR / "wf_next.json"

needs_patch = pytest.mark.skipif(
    not PATCH_PATH.is_file(),
    reason=f"the split-dependencies ops patch is not available: {PATCH_PATH}",
)
needs_export = pytest.mark.skipif(
    not EXPORT_COPY.is_file(),
    reason=f"live export copy is not available: {EXPORT_COPY}",
)
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

# a minimal stand-in for the Create Part Missions node: the map body carries
# the anchor line the patch inserts its depends_on into, and the whole snippet
# runs standalone in node
PART_CODE = "\n".join(
    [
        "const partMissions = [{ id: 'm1' }, { id: 'm1-p2' }, { id: 'm1-p3' }];",
        "const partPlan = (part, k) => ({ part: part.id, k: k });",
        "const bodies = partMissions.map((part, k) => {",
        "  return {",
        "    mission_id: part.id,",
        "    plan: partPlan(part, k),",
        "  };",
        "});",
        "return bodies.map((body) => [body.mission_id, body.depends_on ?? []]);",
    ]
)


def load_patch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("split_dependencies_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_workflow() -> dict[str, Any]:
    """One workflow holding the anchored Create Part Missions node."""
    return {"nodes": [{"name": "Create Part Missions", "parameters": {"jsCode": PART_CODE}}]}


def run_node(code: str) -> list[Any]:
    """Run one jsCode body in node and read back the JSON it returns."""
    done = subprocess.run(
        [
            "node",
            "-e",
            "const f = new Function(require('fs').readFileSync(0, 'utf8'));"
            "process.stdout.write(JSON.stringify(f()));",
        ],
        input=code,
        capture_output=True,
        text=True,
        check=True,
    )
    return list(json.loads(done.stdout))


@needs_patch
@needs_node
def test_split_dependencies_part_chain_runs_the_patched_node_code() -> None:
    """Part 1 depends on nothing, part 2 on part 1, part 3 on part 2, proved by
    running the patched node code in node."""
    patch = load_patch()
    workflow = fixture_workflow()
    assert patch.patch(workflow) is True
    code = workflow["nodes"][0]["parameters"]["jsCode"]
    assert patch.MARKER in code
    assert run_node(code) == [["m1", []], ["m1-p2", ["m1"]], ["m1-p3", ["m1-p2"]]]


@needs_patch
@needs_export
def test_split_dependencies_patch_on_an_export_copy_adds_the_part_line(tmp_path: Path) -> None:
    """The patch on a copy of the live export inserts the depends_on line
    exactly once, and never touches the export file itself."""
    source_sha = hashlib.sha256(EXPORT_COPY.read_bytes()).hexdigest()
    copy = tmp_path / "wf_copy.json"
    shutil.copyfile(EXPORT_COPY, copy)
    patch = load_patch()
    workflow = json.loads(copy.read_text(encoding="utf-8"))
    changed = patch.patch(workflow)
    assert isinstance(changed, bool)
    code = next(
        node["parameters"]["jsCode"]
        for node in workflow["nodes"]
        if node.get("name") == "Create Part Missions"
    )
    assert code.count(patch.MARKER) == 1
    assert "depends_on: partMissions.slice(0, k).slice(-1).map((p) => p.id)," in code
    assert hashlib.sha256(EXPORT_COPY.read_bytes()).hexdigest() == source_sha


@needs_patch
@needs_export
def test_split_dependencies_patch_applied_twice_gives_the_same_bytes(tmp_path: Path) -> None:
    """The CLI run twice on one temporary copy leaves the bytes identical and
    the second run says the file is already patched."""
    source_sha = hashlib.sha256(EXPORT_COPY.read_bytes()).hexdigest()
    copy = tmp_path / "wf_copy.json"
    shutil.copyfile(EXPORT_COPY, copy)
    first = subprocess.run(
        [sys.executable, str(PATCH_PATH), str(copy), str(copy)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "patched" in first.stdout
    bytes_after_first = copy.read_bytes()
    second = subprocess.run(
        [sys.executable, str(PATCH_PATH), str(copy), str(copy)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "already patched" in second.stdout
    assert copy.read_bytes() == bytes_after_first
    assert hashlib.sha256(EXPORT_COPY.read_bytes()).hexdigest() == source_sha


@needs_patch
def test_split_dependencies_patch_second_call_changes_nothing() -> None:
    patch = load_patch()
    workflow = fixture_workflow()
    assert patch.patch(workflow) is True
    assert patch.patch(workflow) is False


@needs_patch
def test_split_dependencies_patch_refuses_a_missing_anchor_naming_the_node() -> None:
    """A workflow without the anchored node exits with the node name."""
    patch = load_patch()
    with pytest.raises(SystemExit) as caught:
        patch.patch({"nodes": []})
    assert "Create Part Missions" in str(caught.value)


@needs_patch
def test_split_dependencies_patch_refuses_a_doubled_anchor_naming_the_anchor() -> None:
    """The anchor line occurring twice exits naming the count and the line."""
    patch = load_patch()
    workflow = fixture_workflow()
    workflow["nodes"][0]["parameters"]["jsCode"] = PART_CODE + "    plan: partPlan(part, k),\n"
    with pytest.raises(SystemExit) as caught:
        patch.patch(workflow)
    assert "anchor occurs 2 times" in str(caught.value)
    assert "partPlan(part, k)" in str(caught.value)


def split_store(features_json: str) -> DependencyStore:
    """A store with only the original split mission m1 queued at position 0."""
    store = DependencyStore()
    store._missions["m1"] = board.Mission(
        mission_id="m1",
        state_namespace="sk1",
        name="Original",
        working_directory="/tmp/x",
        updated_at=PINNING_STAMP,
    )
    store._queue["m1"] = board.QueueEntry(position=0, mission_id="m1", enabled=True)
    store._queue_order.append("m1")
    store.upsert_live_state(
        "sk1",
        None,
        plan={"tasks": [{"id": "T1", "objective": "one"}]},
        status="active",
    )
    store.update_hermes_config(None, features_json=features_json)
    return store


@pytest.fixture
def split_store_on() -> DependencyStore:
    return split_store(FLAG_ON_JSON)


@pytest.fixture
def split_store_off() -> DependencyStore:
    return split_store(FLAG_OFF_JSON)


@pytest.fixture
async def split_client_on(
    settings: Settings, split_store_on: DependencyStore
) -> AsyncGenerator[AsyncClient, None]:
    async for client in client_for_store(settings, split_store_on):
        yield client


@pytest.fixture
async def split_client_off(
    settings: Settings, split_store_off: DependencyStore
) -> AsyncGenerator[AsyncClient, None]:
    async for client in client_for_store(settings, split_store_off):
        yield client


async def test_split_dependencies_create_route_stores_depends_on_with_the_flag_on(
    split_client_on: AsyncClient, split_store_on: DependencyStore
) -> None:
    """POST /missions/create with the fields a split part sends plus
    depends_on: the ids are stored and the answer carries them."""
    answer = await split_client_on.post(
        "/api/board/missions/create",
        json={
            "mission_id": "m1-p2",
            "name": "Original (part 2 of 2)",
            "plan": {"tasks": [{"id": "T2", "objective": "second part"}]},
            "depends_on": ["m1"],
            "who": "adaptive_split",
        },
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["mission"]["mission_id"] == "m1-p2"
    assert body["depends_on"] == ["m1"]
    assert body["dependency_state"] == "waiting"
    assert split_store_on.list_dependencies("m1-p2") == ["m1"]


async def test_split_dependencies_create_route_ignores_depends_on_with_the_flag_off(
    split_client_off: AsyncClient, split_store_off: DependencyStore
) -> None:
    """The same part body with the flag off: the answer has no dependency
    fields and nothing is stored."""
    answer = await split_client_off.post(
        "/api/board/missions/create",
        json={
            "mission_id": "m1-p2",
            "name": "Original (part 2 of 2)",
            "plan": {"tasks": [{"id": "T2", "objective": "second part"}]},
            "depends_on": ["m1"],
            "who": "adaptive_split",
        },
    )
    assert answer.status_code == 200
    body = answer.json()
    assert "depends_on" not in body
    assert "dependency_state" not in body
    assert split_store_off.list_dependencies("m1-p2") == []


def test_split_dependencies_part_without_stored_field_reads_empty_and_is_due() -> None:
    """A queue entry written without a depends_on value (the pre-patch split
    missions): the store verb reads an empty list and the rule says due."""
    entry = {"mission_id": "m1-p2", "position": 1, "enabled": True}
    verdict = BD.classify_queue([entry], {"m1": "queued"})
    assert verdict["m1-p2"] == {"state": "due", "reason": "", "depends_on": []}
    assert BD.build_dependencies(None) == []
