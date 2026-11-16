"""Characterization while the mission-dependency flag is off: the exact wire
bytes of the queue list and one mission answer are pinned, so the dependency
change must reproduce them byte-for-byte until the flag is turned on.

Fixtures are the ones the board API tests use (``board_client`` from
``tests/test_board_api.py``: a seeded ``InMemoryBoardStore``, never the live
database).

The second half exercises the pure rules of ``board_dependencies`` directly:
that module reads no storage, so the queue and the Live State mapping are built
by hand here.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import ahawr_runner.board_dependencies as BD
from ahawr_runner import board
from ahawr_runner.api import create_app
from ahawr_runner.board_pg import DSN_ENV
from ahawr_runner.config import Settings
from ahawr_runner.dashboard import QA_FIXTURE_ENV, DashboardSettings, create_dashboard_app
from tests.test_board_api import board_client

PINNING_STAMP = "2026-01-01T00:00:00+00:00"  # every now() stamp in the fixture lands on this
FLAG_ON_JSON = '{"mission_dependencies": {"enabled": true}}'
FLAG_OFF_JSON = '{"mission_dependencies": {"enabled": false}}'  # the same flag pinned off
DASHBOARD_HEADER = {"X-AHAWR-Dashboard": "1"}  # the header the dashboard gate requires

EXPECTED_QUEUE_BYTES = (
    b'{"queue":[{"position":0,"mission_id":"m1","enabled":true,"attempts_per_task":null,'
    b'"retry_failed":false,"note":null},{"position":1,"mission_id":"m2","enabled":true,'
    b'"attempts_per_task":5.0,"retry_failed":true,"note":"next"}],'
    b'"version":"2026-01-01T00:00:00+00:00"}'
)

EXPECTED_MISSION_BYTES = (
    b'{"mission":{"mission_id":"m1","state_namespace":"sk1","name":"Source","objective":'
    b'"","working_directory":"/tmp/x","version":null,"enabled":true,"rules":["r1"],'
    b'"acceptance_criteria":["c1"],"retrieval_corpora":["corpus"],"overrides_json":null,'
    b'"created_at":null,"updated_at":"2026-01-01T00:00:00+00:00"},'
    b'"live_state":{"state_key":"sk1","worker_session_id":null,'
    b'"plan":{"tasks":[{"id":"T1","objective":"one"}]},"task_index":null,"task_attempt":null,'
    b'"status":"queued","review_feedback":null,"last_review_score":null,"last_review_reason":null,'
    b'"updated_at":"2026-01-01T00:00:00+00:00","reviewer_session_id":null,"reviewer_run_id":null,'
    b'"reviewer_status":null,"worker_run_id":null,"worker_status":null,"architect_run_id":null,'
    b'"architect_status":null,"architect_session_id":null},'
    b'"task_views":[{"task_id":"T1","task_index":0,"title":"","objective":"one",'
    b'"status":"running","attempt":null,"review_score":null,"review_reason":null}],'
    b'"config":{"required_files":["/tmp/x/a.md"],"hints":"HINTS"},"defaults":{},'
    b'"task_overrides":[]}'
)


@pytest.fixture
def pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze board.now so the live-state stamp and the queue version are fixed."""
    monkeypatch.setattr(board, "now", lambda: PINNING_STAMP)


# pytest resolves a fixture by the parameter name, so the imported fixture gets
# bound under the name the byte tests take instead of shadowing the import.
board_api_client = board_client
board_api_client_plain = board_client


async def test_flag_off_depend_queue_answer_bytes(
    pinned_clock: None, board_api_client: AsyncClient
) -> None:
    """GET /api/board/queue with the flag off: the fixture mission m1 plus one
    added waiting entry, in position order, with the queue version stamp."""
    added = await board_api_client.post(
        "/api/board/queue/add",
        json={
            "mission_id": "m2",
            "attempts_per_task": 5.0,
            "retry_failed": True,
            "note": "next",
        },
    )
    assert added.status_code == 200
    queue = await board_api_client.get("/api/board/queue")
    assert queue.status_code == 200
    assert queue.content == EXPECTED_QUEUE_BYTES


async def test_flag_off_depend_mission_answer_bytes(
    pinned_clock: None, board_api_client: AsyncClient
) -> None:
    """GET /api/board/missions/m1 with the flag off: mission row, live state,
    task views, config, defaults and task overrides, byte-for-byte."""
    mission = await board_api_client.get("/api/board/missions/m1")
    assert mission.status_code == 200
    assert mission.content == EXPECTED_MISSION_BYTES


# ---------------------------------------------------------------------------
# the flag turned on: the queue and the mission answer carry the dependency
# fields, and the mission save route validates and writes depends_on
# ---------------------------------------------------------------------------


class DependencyStore(board.InMemoryBoardStore):
    """InMemoryBoardStore plus the ``depends_on`` column the Pg store owns."""

    def __init__(self) -> None:
        super().__init__()
        self._depends_on: dict[str, list[str]] = {}

    def list_dependencies(self, mission_id: str) -> list[str]:
        return list(self._depends_on.get(mission_id, []))

    def set_dependencies(
        self, mission_id: str, depends_on: Any, expected_version: str | None
    ) -> list[board.QueueEntry]:
        if mission_id not in self._queue:
            raise KeyError(f"mission {mission_id!r} not in the queue")
        self._depends_on[mission_id] = BD.build_dependencies(depends_on)
        return self.queue()


@pytest.fixture
def dependency_store() -> DependencyStore:
    """The dependency fixture: the ``mission_dependencies`` flag on and two queued
    missions m1 (position 0) and m2 (position 1), both still ``queued``."""
    store = DependencyStore()
    store._missions["m1"] = board.Mission(
        mission_id="m1",
        state_namespace="sk1",
        name="Source",
        working_directory="/tmp/x",
        updated_at=PINNING_STAMP,
    )
    store._missions["m2"] = board.Mission(
        mission_id="m2",
        state_namespace="sk2",
        name="Helper",
        working_directory="/tmp/y",
        updated_at=PINNING_STAMP,
    )
    store._queue["m1"] = board.QueueEntry(position=0, mission_id="m1", enabled=True)
    store._queue["m2"] = board.QueueEntry(position=1, mission_id="m2", enabled=True)
    store._queue_order.extend(["m1", "m2"])
    store.upsert_live_state(
        "sk1", None, plan={"tasks": [{"id": "T1", "objective": "one"}]}, status="queued"
    )
    store.upsert_live_state(
        "sk2", None, plan={"tasks": [{"id": "T2", "objective": "two"}]}, status="queued"
    )
    store.update_hermes_config(None, features_json=FLAG_ON_JSON)
    return store


async def client_for_store(
    settings: Settings, store: board.BoardStore
) -> AsyncGenerator[AsyncClient, None]:
    """The board API over one store: the wiring every flag-on route fixture reuses."""
    app = create_app(settings)
    app.state.board_store = store
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def dependency_client(
    settings: Settings, dependency_store: DependencyStore
) -> AsyncGenerator[AsyncClient, None]:
    """The board API wired to ``dependency_store``, with the flag on."""
    async for client in client_for_store(settings, dependency_store):
        yield client


@pytest.fixture
def chain_store() -> DependencyStore:
    """Three queued missions m1, m2 and m3 with the flag on: m2 waits on m1 and m3
    waits on m2, so m1 has a direct dependent and a transitive one."""
    store = DependencyStore()
    for position, mission_id in enumerate(("m1", "m2", "m3")):
        store._missions[mission_id] = board.Mission(
            mission_id=mission_id,
            state_namespace=f"sk{position}",
            name=f"Mission {mission_id}",
            working_directory="/tmp/x",
            updated_at=PINNING_STAMP,
        )
        store._queue[mission_id] = board.QueueEntry(
            position=position, mission_id=mission_id, enabled=True
        )
        store._queue_order.append(mission_id)
        store.upsert_live_state(
            f"sk{position}",
            None,
            plan={"tasks": [{"id": "T1", "objective": "one"}]},
            status="queued",
        )
    store.set_dependencies("m2", ["m1"], None)
    store.set_dependencies("m3", ["m2"], None)
    store.update_hermes_config(None, features_json=FLAG_ON_JSON)
    return store


@pytest.fixture
async def chain_client(
    settings: Settings, chain_store: DependencyStore
) -> AsyncGenerator[AsyncClient, None]:
    """The board API wired to ``chain_store``: the three-mission dependency chain."""
    async for client in client_for_store(settings, chain_store):
        yield client


@pytest.fixture
def dashboard_dependency_client(
    monkeypatch: pytest.MonkeyPatch, dependency_store: DependencyStore
) -> Iterator[TestClient]:
    """The dashboard app over the same store: it is the one that refuses every
    ``POST`` under ``/api/board`` without the ``X-AHAWR-Dashboard`` header."""
    monkeypatch.delenv(DSN_ENV, raising=False)
    monkeypatch.delenv(QA_FIXTURE_ENV, raising=False)
    app = create_dashboard_app(DashboardSettings())
    app.state.board_store = dependency_store
    with TestClient(app, base_url="http://localhost") as client:
        yield client


async def test_flag_on_queue_answer_carries_dependency_state(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """GET /api/board/queue with the flag on: every entry has depends_on,
    dependency_state and dependency_reason; m2 waiting on the still-queued m1."""
    dependency = await dependency_client.post(
        "/api/board/missions/m2", json={"depends_on": ["m1", "m1", " "], "who": "op"}
    )
    assert dependency.status_code == 200
    queue = await dependency_client.get("/api/board/queue")
    assert queue.status_code == 200
    rows = queue.json()["queue"]
    assert [row["mission_id"] for row in rows] == ["m1", "m2"]
    assert rows[0]["depends_on"] == []
    assert rows[0]["dependency_state"] == "due"
    assert rows[0]["dependency_reason"] == ""
    assert rows[1]["depends_on"] == ["m1"]
    assert rows[1]["dependency_state"] == "waiting"
    assert rows[1]["dependency_reason"] == "waiting for m1"


async def test_flag_on_mission_answer_carries_dependency_state(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """GET /api/board/missions/m2 after the save: the same three fields, and the
    mission row itself keeps its old shape (depends_on is not a mission column)."""
    saved = await dependency_client.post(
        "/api/board/missions/m2", json={"depends_on": ["m1"], "who": "op"}
    )
    assert saved.status_code == 200
    assert saved.json()["depends_on"] == ["m1"]
    assert saved.json()["dependency_state"] == "waiting"
    assert saved.json()["dependency_reason"] == "waiting for m1"
    assert "depends_on" not in saved.json()["mission"]
    mission = await dependency_client.get("/api/board/missions/m2")
    assert mission.status_code == 200
    assert mission.json()["depends_on"] == ["m1"]
    assert mission.json()["dependency_state"] == "waiting"
    assert mission.json()["dependency_reason"] == "waiting for m1"
    assert "depends_on" not in mission.json()["mission"]


async def test_flag_on_dependency_context_architect_dependencies_block(
    pinned_clock: None, chain_store: DependencyStore, chain_client: AsyncClient
) -> None:
    """The Architect block of m3, which waits on m1 and m2: every dependency is
    named by id, name and stored state; m1 has a report task logged in the event
    rows, m2 only a plan summary, and the rule sentence closes the block.  The
    same block rides the ``…/plan`` answer."""
    chain_store.set_dependencies("m3", ["m1", "m2"], None)
    chain_store.upsert_live_state(
        "sk0",
        None,
        plan={
            "summary": "m1 summary",
            "tasks": [{"id": "T1", "title": "Mission report", "objective": "one"}],
        },
        status="completed",
    )
    chain_store.append_event(
        "sk0",
        {"task_id": "T1", "event_type": "worker_completed", "worker_output": "m1 final report"},
    )
    chain_store.upsert_live_state(
        "sk1", None, plan={"summary": "m2 summary", "tasks": []}, status="active"
    )
    # an output of a task that is not the report task is never taken as the report
    chain_store.append_event(
        "sk1", {"task_id": "T9", "event_type": "worker_completed", "worker_output": "not a report"}
    )

    mission = await chain_client.get("/api/board/missions/m3")
    assert mission.status_code == 200
    block = mission.json()["dependency_context"]
    assert block.startswith(BD.DEPENDENCY_BLOCK_HEADING)
    assert 'm1 "Mission m1" [completed]: m1 final report' in block
    assert 'm2 "Mission m2" [active]: m2 summary' in block
    assert "Rule: " + BD.ARCHITECT_DEPENDENCY_RULE in block
    assert len(block) <= BD.ARCHITECT_BLOCK_MAX_CHARS

    plan = await chain_client.get("/api/board/missions/m3/plan")
    assert plan.status_code == 200
    assert plan.json()["dependency_context"] == block


async def test_flag_off_answer_has_no_dependency_context(
    pinned_clock: None, board_api_client: AsyncClient
) -> None:
    """With the flag off no Architect block is added, even for a mission that
    would have dependencies in the store."""
    mission = await board_api_client.get("/api/board/missions/m1")
    assert mission.status_code == 200
    assert "dependency_context" not in mission.json()
    plan = await board_api_client.get("/api/board/missions/m1/plan")
    assert plan.status_code == 200
    assert "dependency_context" not in plan.json()


async def test_flag_on_save_rejects_an_unknown_dependency_id(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """POST /api/board/missions/m1 with depends_on ["ghost"]: 400 naming ghost."""
    answer = await dependency_client.post(
        "/api/board/missions/m1", json={"depends_on": ["ghost"], "who": "op"}
    )
    assert answer.status_code == 400
    assert "ghost" in answer.json()["error"]["message"]


async def test_flag_on_save_rejects_a_dependency_cycle(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """m2 depends on m1, then m1 asks for m2: 400 naming the cycle m1 -> m2 -> m1."""
    await dependency_client.post("/api/board/missions/m2", json={"depends_on": ["m1"], "who": "op"})
    answer = await dependency_client.post(
        "/api/board/missions/m1", json={"depends_on": ["m2"], "who": "op"}
    )
    assert answer.status_code == 400
    assert "m1 -> m2 -> m1" in answer.json()["error"]["message"]


async def test_flag_on_save_rejects_a_self_dependency(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    answer = await dependency_client.post(
        "/api/board/missions/m1", json={"depends_on": ["m1"], "who": "op"}
    )
    assert answer.status_code == 400
    assert "cannot depend on itself" in answer.json()["error"]["message"]


async def test_flag_on_save_stops_before_the_dependency_write_on_a_conflict(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """A stale expected_version aborts the save: the 409 leaves the stored
    dependency graph empty, so a conflict never half-writes a dependency."""
    answer = await dependency_client.post(
        "/api/board/missions/m2",
        json={
            "name": "Helper",
            "depends_on": ["m1"],
            "who": "op",
            "expected_version": "2020-01-01T00:00:00+00:00",
        },
    )
    assert answer.status_code == 409
    queue = await dependency_client.get("/api/board/queue")
    assert queue.json()["queue"][1]["depends_on"] == []
    assert queue.json()["queue"][1]["dependency_state"] == "due"


async def test_flag_on_dependency_save_is_aborted_before_the_dependency_is_written(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """A valid ``depends_on`` sent with an invalid ``overrides_json`` is refused as
    one save: the dependency graph stays empty, nothing is half-written."""
    answer = await dependency_client.post(
        "/api/board/missions/m2",
        json={
            "depends_on": ["m1"],
            "overrides_json": '{"retrieval": {"timeout_ms": 500}}',
            "who": "op",
        },
    )
    assert answer.status_code == 400
    assert "timeout_ms" in answer.json()["error"]["message"]
    queue = await dependency_client.get("/api/board/queue")
    assert queue.json()["queue"][1]["depends_on"] == []
    assert queue.json()["queue"][1]["dependency_state"] == "due"


async def test_flag_on_queue_answer_reports_a_blocked_dependency_with_reason(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dependency_client: AsyncClient,
) -> None:
    """m2 waits on m1; m1's Live State says ``failed``: the queue row for m2 is
    ``blocked`` naming m1, while m1 (no dependencies of its own) stays ``due``."""
    saved = await dependency_client.post(
        "/api/board/missions/m2", json={"depends_on": ["m1"], "who": "op"}
    )
    assert saved.status_code == 200
    dependency_store.upsert_live_state("sk1", None, status="failed")
    queue = await dependency_client.get("/api/board/queue")
    assert queue.status_code == 200
    rows = queue.json()["queue"]
    assert rows[0]["depends_on"] == []
    assert rows[0]["dependency_state"] == "due"
    assert rows[1]["depends_on"] == ["m1"]
    assert rows[1]["dependency_state"] == "blocked"
    assert rows[1]["dependency_reason"] == "blocked by m1: a dependency failed"


async def test_flag_on_queue_answer_reports_a_due_dependency_once_the_dependency_completes(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dependency_client: AsyncClient,
) -> None:
    """m1's Live State turns ``completed``: m2 stops waiting and becomes ``due``
    with an empty reason, the stored ids staying the same."""
    saved = await dependency_client.post(
        "/api/board/missions/m2", json={"depends_on": ["m1"], "who": "op"}
    )
    assert saved.status_code == 200
    dependency_store.upsert_live_state("sk1", None, status="completed")
    queue = await dependency_client.get("/api/board/queue")
    row = queue.json()["queue"][1]
    assert row["dependency_state"] == "due"
    assert row["dependency_reason"] == ""
    assert row["depends_on"] == ["m1"]


async def test_flag_on_save_accepts_a_dependency_completed_and_out_of_the_queue(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dependency_client: AsyncClient,
) -> None:
    """m4 finished earlier and left the queue: it is still known, so the save is
    accepted and m2 reads ``due`` against a dependency that is not queued."""
    dependency_store._missions["m4"] = board.Mission(
        mission_id="m4",
        state_namespace="sk4",
        name="Done",
        working_directory="/tmp/z",
        updated_at=PINNING_STAMP,
    )
    dependency_store.upsert_live_state("sk4", None, plan={"tasks": []}, status="completed")
    saved = await dependency_client.post(
        "/api/board/missions/m2", json={"depends_on": ["m4"], "who": "op"}
    )
    assert saved.status_code == 200
    assert saved.json()["depends_on"] == ["m4"]
    assert saved.json()["dependency_state"] == "due"
    assert saved.json()["dependency_reason"] == ""
    queue = await dependency_client.get("/api/board/queue")
    assert [row["mission_id"] for row in queue.json()["queue"]] == ["m1", "m2"]
    assert queue.json()["queue"][1]["depends_on"] == ["m4"]


async def test_flag_on_save_of_an_empty_dependency_list_clears_the_dependency(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """Saving ``[]`` after m2 waited on m1 drops the dependency: the row reads
    ``due`` with an empty reason and no ids, unblocking the entry."""
    first = await dependency_client.post(
        "/api/board/missions/m2", json={"depends_on": ["m1"], "who": "op"}
    )
    assert first.status_code == 200
    assert first.json()["dependency_state"] == "waiting"
    cleared = await dependency_client.post(
        "/api/board/missions/m2", json={"depends_on": [], "who": "op"}
    )
    assert cleared.status_code == 200
    assert cleared.json()["depends_on"] == []
    assert cleared.json()["dependency_state"] == "due"
    assert cleared.json()["dependency_reason"] == ""
    queue = await dependency_client.get("/api/board/queue")
    assert queue.json()["queue"][1]["depends_on"] == []
    assert queue.json()["queue"][1]["dependency_state"] == "due"


async def test_flag_on_save_rejects_a_dependency_value_that_is_not_a_list(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """``depends_on: "m1"`` (a bare string) is refused with 400 naming the type."""
    answer = await dependency_client.post(
        "/api/board/missions/m2", json={"depends_on": "m1", "who": "op"}
    )
    assert answer.status_code == 400
    assert "must be a list of mission ids" in answer.json()["error"]["message"]


def test_flag_on_dependency_save_without_the_dashboard_header_is_403(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dashboard_dependency_client: TestClient,
) -> None:
    """The dashboard gate refuses the dependency ``POST`` before the route runs: the
    stored graph stays empty, and the same save with the header works."""
    refused = dashboard_dependency_client.post(
        "/api/board/missions/m2", json={"depends_on": ["m1"], "who": "op"}
    )
    assert refused.status_code == 403
    assert refused.json() == {"error": "missing X-AHAWR-Dashboard header"}
    assert dependency_store.list_dependencies("m2") == []
    saved = dashboard_dependency_client.post(
        "/api/board/missions/m2",
        json={"depends_on": ["m1"], "who": "op"},
        headers=DASHBOARD_HEADER,
    )
    assert saved.status_code == 200
    assert saved.json()["depends_on"] == ["m1"]
    assert dependency_store.list_dependencies("m2") == ["m1"]


async def test_flag_on_create_places_the_mission_after_its_dependency(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dependency_client: AsyncClient,
) -> None:
    """POST /missions/create with ``depends_on: ["m1"]``: the ids are stored and
    the new mission is queued directly after m1, ahead of m2 (the fields a split
    part sends are what make this a create without a source)."""
    answer = await dependency_client.post(
        "/api/board/missions/create",
        json={"mission_id": "m3", "name": "Part one", "depends_on": ["m1"]},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["mission"]["mission_id"] == "m3"
    assert body["depends_on"] == ["m1"]
    assert body["dependency_state"] == "waiting"
    assert body["dependency_reason"] == "waiting for m1"
    assert [(row["position"], row["mission_id"]) for row in body["queue"]] == [
        (0, "m1"),
        (1, "m3"),
        (2, "m2"),
    ]
    assert dependency_store.list_dependencies("m3") == ["m1"]


async def test_flag_on_create_uses_the_last_queued_dependency_over_a_sent_position(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """A sent ``position`` is overridden by the dependency placement: m4 waits on
    m2 (position 1), so it lands after m2, not at the top."""
    answer = await dependency_client.post(
        "/api/board/missions/create",
        json={"mission_id": "m4", "name": "Later part", "position": 0, "depends_on": ["m2"]},
    )
    assert answer.status_code == 200
    assert [(row["position"], row["mission_id"]) for row in answer.json()["queue"]] == [
        (0, "m1"),
        (1, "m2"),
        (2, "m4"),
    ]


async def test_flag_on_create_with_a_completed_dependency_keeps_the_sent_position(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dependency_client: AsyncClient,
) -> None:
    """A dependency that is already completed does not push the new mission down:
    m1 (completed) is skipped by ``_dependency_queue_position``, so the sent
    ``position: 0`` stands and m3 goes to the top of the queue, and it is ``due``."""
    dependency_store.upsert_live_state(
        "sk1", None, plan={"tasks": [{"id": "T1", "objective": "one"}]}, status="completed"
    )
    answer = await dependency_client.post(
        "/api/board/missions/create",
        json={"mission_id": "m3", "name": "Part one", "position": 0, "depends_on": ["m1"]},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert [(row["position"], row["mission_id"]) for row in body["queue"]] == [
        (0, "m3"),
        (1, "m1"),
        (2, "m2"),
    ]
    assert body["dependency_state"] == "due"
    assert dependency_store.list_dependencies("m3") == ["m1"]


async def test_flag_on_create_with_only_completed_dependencies_keeps_the_default_position(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dependency_client: AsyncClient,
) -> None:
    """No ``position`` sent and every dependency (m1) already completed: the new
    mission m3 lands at the end of the queue, the store's default, and reads due."""
    dependency_store.upsert_live_state(
        "sk1", None, plan={"tasks": [{"id": "T1", "objective": "one"}]}, status="completed"
    )
    answer = await dependency_client.post(
        "/api/board/missions/create",
        json={"mission_id": "m3", "name": "Part one", "depends_on": ["m1"]},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert [(row["position"], row["mission_id"]) for row in body["queue"]] == [
        (0, "m1"),
        (1, "m2"),
        (2, "m3"),
    ]
    assert body["depends_on"] == ["m1"]
    assert body["dependency_state"] == "due"
    assert dependency_store.list_dependencies("m3") == ["m1"]


async def test_flag_on_create_keeps_a_sent_position_already_after_the_dependency(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """The sent position is kept when it is already later than the dependency: m3
    waits on m1 (position 0) and is sent at 2, so it stays at 2 (after m2)."""
    answer = await dependency_client.post(
        "/api/board/missions/create",
        json={"mission_id": "m3", "name": "Part one", "position": 2, "depends_on": ["m1"]},
    )
    assert answer.status_code == 200
    assert [(row["position"], row["mission_id"]) for row in answer.json()["queue"]] == [
        (0, "m1"),
        (1, "m2"),
        (2, "m3"),
    ]


async def test_flag_on_create_with_an_unknown_dependency_creates_nothing(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """``depends_on: ["ghost"]`` is refused with 400 naming ghost: no mission row,
    no queue entry, no dependency write."""
    refused = await dependency_client.post(
        "/api/board/missions/create",
        json={"mission_id": "m3", "name": "Part one", "depends_on": ["ghost"]},
    )
    assert refused.status_code == 400
    assert "ghost" in refused.json()["error"]["message"]
    queue = await dependency_client.get("/api/board/queue")
    assert [row["mission_id"] for row in queue.json()["queue"]] == ["m1", "m2"]
    missing = await dependency_client.get("/api/board/missions/m3")
    assert missing.status_code == 404


async def test_flag_on_create_with_a_self_dependency_creates_nothing(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dependency_client: AsyncClient,
) -> None:
    """Creating m3 with ``depends_on: ["m3"]`` is refused with 400 naming the self
    reference: the queue keeps its length of two and m3 has no stored dependency."""
    refused = await dependency_client.post(
        "/api/board/missions/create",
        json={"mission_id": "m3", "name": "Part one", "depends_on": ["m3", "m3"]},
    )
    assert refused.status_code == 400
    assert "cannot depend on itself" in refused.json()["error"]["message"]
    queue = await dependency_client.get("/api/board/queue")
    assert len(queue.json()["queue"]) == 2
    assert [row["mission_id"] for row in queue.json()["queue"]] == ["m1", "m2"]
    assert dependency_store.list_dependencies("m3") == []
    missing = await dependency_client.get("/api/board/missions/m3")
    assert missing.status_code == 404


async def test_flag_on_create_with_a_dependency_that_closes_a_cycle_creates_nothing(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dependency_client: AsyncClient,
) -> None:
    """m2 already waits on m3 (stored before m3 exists, as a split part does), so
    creating m3 with ``depends_on: ["m2"]`` closes the cycle: 400 naming both ids,
    and the queue still holds only m1 and m2."""
    dependency_store.set_dependencies("m2", ["m3"], None)
    refused = await dependency_client.post(
        "/api/board/missions/create",
        json={"mission_id": "m3", "name": "Part one", "depends_on": ["m2"]},
    )
    assert refused.status_code == 400
    message = refused.json()["error"]["message"]
    assert "cycle" in message and "m3" in message and "m2" in message
    queue = await dependency_client.get("/api/board/queue")
    assert [row["mission_id"] for row in queue.json()["queue"]] == ["m1", "m2"]
    missing = await dependency_client.get("/api/board/missions/m3")
    assert missing.status_code == 404


async def test_flag_on_copy_keeps_the_dependencies_of_its_source(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dependency_client: AsyncClient,
) -> None:
    """A copy of m1 (which waits on m2) carries the same ``depends_on`` and is
    queued after m2, the last dependency it still waits on."""
    saved = await dependency_client.post(
        "/api/board/missions/m1", json={"depends_on": ["m2"], "who": "op"}
    )
    assert saved.status_code == 200
    copied = await dependency_client.post(
        "/api/board/missions/create",
        json={"mission_id": "m4", "source_mission_id": "m1"},
    )
    assert copied.status_code == 200
    body = copied.json()
    assert body["depends_on"] == ["m2"]
    assert [(row["position"], row["mission_id"]) for row in body["queue"]] == [
        (0, "m1"),
        (1, "m2"),
        (2, "m4"),
    ]
    assert dependency_store.list_dependencies("m4") == ["m2"]


def test_flag_on_create_with_a_dependency_without_the_dashboard_header_is_403(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dashboard_dependency_client: TestClient,
) -> None:
    """The dashboard gate refuses the create ``POST`` before the route runs: m3 is
    neither in the queue nor carrying a dependency, and the same create with the
    header succeeds and places m3 after its dependency m1."""
    refused = dashboard_dependency_client.post(
        "/api/board/missions/create",
        json={"mission_id": "m3", "name": "Part one", "depends_on": ["m1"]},
    )
    assert refused.status_code == 403
    assert refused.json() == {"error": "missing X-AHAWR-Dashboard header"}
    queue = dashboard_dependency_client.get("/api/board/queue")
    assert [row["mission_id"] for row in queue.json()["queue"]] == ["m1", "m2"]
    assert dependency_store.list_dependencies("m3") == []
    created = dashboard_dependency_client.post(
        "/api/board/missions/create",
        json={"mission_id": "m3", "name": "Part one", "depends_on": ["m1"]},
        headers=DASHBOARD_HEADER,
    )
    assert created.status_code == 200
    assert created.json()["depends_on"] == ["m1"]
    assert [(row["position"], row["mission_id"]) for row in created.json()["queue"]] == [
        (0, "m1"),
        (1, "m3"),
        (2, "m2"),
    ]
    assert dependency_store.list_dependencies("m3") == ["m1"]


async def test_flag_off_create_and_copy_keep_the_sent_position_and_answer(
    pinned_clock: None, board_api_client: AsyncClient
) -> None:
    """With the flag off the create route drops ``depends_on`` like any unknown
    field: the answer keys, the sent position and the queue order are unchanged."""
    created = await board_api_client.post(
        "/api/board/missions/create",
        json={
            "mission_id": "m2",
            "source_mission_id": "m1",
            "position": 0,
            "depends_on": ["ghost", 3],
        },
    )
    assert created.status_code == 200
    assert set(created.json()) == {"mission", "live_state", "queue", "diff"}
    queue = await board_api_client.get("/api/board/queue")
    assert queue.status_code == 200
    assert [(row["position"], row["mission_id"]) for row in queue.json()["queue"]] == [
        (0, "m2"),
        (1, "m1"),
    ]
    assert "depends_on" not in queue.json()["queue"][0]
    assert "depends_on" not in created.json()["mission"]


async def test_flag_off_create_with_depends_on_returns_the_same_bytes_as_without_it(
    pinned_clock: None,
    board_api_client: AsyncClient,
    board_api_client_plain: AsyncClient,
) -> None:
    """With the flag off two fresh stores get the same create body, one carrying
    ``depends_on``: the answers are byte-for-byte identical and the mission still
    lands at the sent position."""
    with_dependency = await board_api_client.post(
        "/api/board/missions/create",
        json={
            "mission_id": "m2",
            "source_mission_id": "m1",
            "position": 0,
            "depends_on": ["m1"],
        },
    )
    without_dependency = await board_api_client_plain.post(
        "/api/board/missions/create",
        json={"mission_id": "m2", "source_mission_id": "m1", "position": 0},
    )
    assert with_dependency.status_code == 200
    assert without_dependency.status_code == 200
    assert with_dependency.content == without_dependency.content
    assert "depends_on" not in with_dependency.json()["mission"]
    queue = await board_api_client.get("/api/board/queue")
    assert [(row["position"], row["mission_id"]) for row in queue.json()["queue"]] == [
        (0, "m2"),
        (1, "m1"),
    ]


async def test_flag_off_dependency_save_with_a_stale_version_is_refused(
    pinned_clock: None, board_api_client: AsyncClient
) -> None:
    """With the flag off the version check still runs: a stale expected_version is a
    409 even though the body carries a depends_on the flag ignores."""
    answer = await board_api_client.post(
        "/api/board/missions/m1",
        json={
            "depends_on": ["m2"],
            "who": "op",
            "expected_version": "2020-01-01T00:00:00+00:00",
        },
    )
    assert answer.status_code == 409
    assert "expected version" in answer.json()["error"]["message"]
    mission = await board_api_client.get("/api/board/missions/m1")
    assert mission.content == EXPECTED_MISSION_BYTES


async def test_flag_off_ignores_a_sent_dependency(
    pinned_clock: None, board_api_client: AsyncClient
) -> None:
    """With the flag off a sent depends_on is dropped like any unknown field: the
    mission answer keeps the pinned bytes and no 400 is raised."""
    answer = await board_api_client.post(
        "/api/board/missions/m1",
        json={
            "depends_on": ["ghost"],
            "who": "op",
            "expected_version": "2026-01-01T00:00:00+00:00",
        },
    )
    assert answer.status_code == 200
    assert set(answer.json()) == {"mission"}
    mission = await board_api_client.get("/api/board/missions/m1")
    assert mission.content == EXPECTED_MISSION_BYTES


# ---------------------------------------------------------------------------
# the moves and the start actions checked against the stored dependencies
# ---------------------------------------------------------------------------


async def test_flag_on_move_before_its_dependency_is_refused_and_keeps_order(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """m2 waits on m1: moving m2 to position 0 answers 403 naming m1, and the queue
    order stays m1, m2 (nothing is written before the refusal)."""
    saved = await dependency_client.post(
        "/api/board/missions/m2", json={"depends_on": ["m1"], "who": "op"}
    )
    assert saved.status_code == 200
    refused = await dependency_client.post(
        "/api/board/queue/move", json={"mission_id": "m2", "to_position": 0}
    )
    assert refused.status_code == 403
    assert "cannot move before its dependency m1" in refused.json()["error"]["message"]
    assert refused.json()["blocked_by"] == ["m1"]
    queue = await dependency_client.get("/api/board/queue")
    assert [row["mission_id"] for row in queue.json()["queue"]] == ["m1", "m2"]


async def test_flag_on_move_a_dependency_after_its_dependent_is_refused(
    pinned_clock: None, dependency_client: AsyncClient
) -> None:
    """Moving m1 (the dependency) to the last position answers 403 listing the
    missions that depend on it, and the order is unchanged."""
    await dependency_client.post(
        "/api/board/missions/m2", json={"depends_on": ["m1"], "who": "op"}
    )
    refused = await dependency_client.post(
        "/api/board/queue/last", json={"mission_id": "m1"}
    )
    assert refused.status_code == 403
    message = refused.json()["error"]["message"]
    assert "cannot move after the missions that depend on it: m2" in message
    assert refused.json()["blocked_by"] == ["m2"]
    queue = await dependency_client.get("/api/board/queue")
    assert [row["mission_id"] for row in queue.json()["queue"]] == ["m1", "m2"]


async def test_flag_on_move_with_move_dependents_carries_the_chain_in_order(
    pinned_clock: None, chain_client: AsyncClient
) -> None:
    """m2 waits on m1, m3 waits on m2: moving m1 out of the first position with
    move_dependents carries m2 and m3 right after it in their existing order."""
    await chain_client.post("/api/board/queue/add", json={"mission_id": "m4"})
    moved = await chain_client.post(
        "/api/board/queue/move",
        json={"mission_id": "m1", "to_position": 3, "move_dependents": True},
    )
    assert moved.status_code == 200
    rows = moved.json()["queue"]
    assert [row["mission_id"] for row in rows] == ["m4", "m1", "m2", "m3"]
    assert [row["position"] for row in rows] == [0, 1, 2, 3]
    queue = await chain_client.get("/api/board/queue")
    assert [row["mission_id"] for row in queue.json()["queue"]] == ["m4", "m1", "m2", "m3"]


async def test_flag_on_move_with_move_dependents_keeps_a_full_chain_untouched(
    pinned_clock: None, chain_client: AsyncClient
) -> None:
    """The whole queue is the group: the carried chain keeps its positions, so the
    move answers 200 with the same order."""
    moved = await chain_client.post(
        "/api/board/queue/move",
        json={"mission_id": "m1", "to_position": 2, "move_dependents": True},
    )
    assert moved.status_code == 200
    assert [row["mission_id"] for row in moved.json()["queue"]] == ["m1", "m2", "m3"]


async def test_flag_on_first_refuses_a_mission_that_still_waits(
    pinned_clock: None, chain_client: AsyncClient
) -> None:
    """"Run next" of m3 (waiting on m2) is a 403 naming the dependency, and m3 keeps
    its position."""
    refused = await chain_client.post("/api/board/queue/first", json={"mission_id": "m3"})
    assert refused.status_code == 403
    assert "waiting for m2" in refused.json()["error"]["message"]
    assert refused.json()["blocked_by"] == ["m2"]
    queue = await chain_client.get("/api/board/queue")
    assert [row["mission_id"] for row in queue.json()["queue"]] == ["m1", "m2", "m3"]


async def test_flag_on_first_refuses_a_mission_blocked_by_a_failed_dependency(
    pinned_clock: None,
    chain_store: DependencyStore,
    chain_client: AsyncClient,
) -> None:
    """m3 waits on m2 whose Live State says ``failed``: the refusal says blocked."""
    chain_store.upsert_live_state("sk1", None, status="failed")
    refused = await chain_client.post(
        "/api/board/queue/first", json={"mission_id": "m3"}
    )
    assert refused.status_code == 403
    assert "blocked" in refused.json()["error"]["message"]
    queue = await chain_client.get("/api/board/queue")
    assert [row["mission_id"] for row in queue.json()["queue"]] == ["m1", "m2", "m3"]


async def test_flag_on_first_with_run_anyway_moves_the_waiting_mission(
    pinned_clock: None, chain_client: AsyncClient
) -> None:
    """run_anyway: true is the operator's override: m3 lands at position 0 and its
    dependents are not carried with it."""
    moved = await chain_client.post(
        "/api/board/queue/first", json={"mission_id": "m3", "run_anyway": True}
    )
    assert moved.status_code == 200
    assert [row["mission_id"] for row in moved.json()["queue"]] == ["m3", "m1", "m2"]


async def test_flag_on_first_of_a_due_mission_is_not_refused(
    pinned_clock: None, chain_client: AsyncClient
) -> None:
    """m1 has no dependencies of its own: "Run next" accepts it and leaves the
    order alone, exactly as with the flag off."""
    moved = await chain_client.post("/api/board/queue/first", json={"mission_id": "m1"})
    assert moved.status_code == 200
    assert [row["mission_id"] for row in moved.json()["queue"]] == ["m1", "m2", "m3"]


async def test_flag_on_start_refuses_a_mission_that_waits_on_another(
    pinned_clock: None, chain_client: AsyncClient
) -> None:
    """"Run now" of m3 is refused before the readiness is collected: the refusal
    names the dependency state and the blocking id."""
    refused = await chain_client.post("/api/board/missions/m3/start")
    assert refused.status_code == 403
    assert refused.json()["dependency_state"] == "waiting"
    assert "waiting for m2" in refused.json()["error"]["message"]
    assert refused.json()["blocked_by"] == ["m2"]


async def test_flag_on_start_with_run_anyway_skips_the_dependency_guard(
    pinned_clock: None, chain_client: AsyncClient
) -> None:
    """run_anyway: true lets the start reach the readiness guard, whose answer
    carries no dependency_state."""
    started = await chain_client.post(
        "/api/board/missions/m3/start", json={"run_anyway": True}
    )
    assert started.status_code == 200
    assert "dependency_state" not in started.json()


async def test_flag_on_move_last_refuses_a_dependency_and_lists_every_dependent(
    pinned_clock: None, chain_client: AsyncClient
) -> None:
    """m2 and m3 both wait on m1: moving m1 to the end answers 403 listing both
    dependents, and the queue order stays as it was."""
    await chain_client.post(
        "/api/board/missions/m3", json={"depends_on": ["m1", "m2"], "who": "op"}
    )
    refused = await chain_client.post("/api/board/queue/last", json={"mission_id": "m1"})
    assert refused.status_code == 403
    message = refused.json()["error"]["message"]
    assert "cannot move after the missions that depend on it: m2, m3" in message
    assert refused.json()["blocked_by"] == ["m2", "m3"]
    queue = await chain_client.get("/api/board/queue")
    assert [row["mission_id"] for row in queue.json()["queue"]] == ["m1", "m2", "m3"]


async def test_flag_on_first_of_a_blocked_mission_with_run_anyway_moves_it(
    pinned_clock: None,
    chain_store: DependencyStore,
    chain_client: AsyncClient,
) -> None:
    """"Run next" of a blocked m3 (m2 failed) with run_anyway lands it at position 0
    with no dependency check, exactly as the flag-off route does."""
    chain_store.upsert_live_state("sk1", None, status="failed")
    moved = await chain_client.post(
        "/api/board/queue/first", json={"mission_id": "m3", "run_anyway": True}
    )
    assert moved.status_code == 200
    assert [row["mission_id"] for row in moved.json()["queue"]] == ["m3", "m1", "m2"]


async def test_flag_on_start_refuses_a_blocked_mission_without_run_anyway(
    pinned_clock: None,
    chain_store: DependencyStore,
    chain_client: AsyncClient,
) -> None:
    """m3 is blocked because its dependency m2 failed: "Run now" answers 403 with
    the blocked state and the blocking id, before the readiness is collected."""
    chain_store.upsert_live_state("sk1", None, status="failed")
    refused = await chain_client.post("/api/board/missions/m3/start")
    assert refused.status_code == 403
    assert refused.json()["dependency_state"] == "blocked"
    assert "blocked by m2" in refused.json()["error"]["message"]
    assert refused.json()["blocked_by"] == ["m2"]


async def test_flag_on_start_of_a_blocked_mission_with_run_anyway_is_accepted(
    pinned_clock: None,
    chain_store: DependencyStore,
    chain_client: AsyncClient,
) -> None:
    """run_anyway: true skips the blocked guard, so the start reaches the readiness
    answer with no dependency_state field."""
    chain_store.upsert_live_state("sk1", None, status="failed")
    started = await chain_client.post(
        "/api/board/missions/m3/start", json={"run_anyway": True}
    )
    assert started.status_code == 200
    assert "dependency_state" not in started.json()


async def test_flag_on_start_of_a_mission_with_no_dependencies_is_accepted(
    pinned_clock: None, chain_client: AsyncClient
) -> None:
    """m1 waits on nothing: "Run now" is not refused and the answer keeps its old
    shape (no dependency_state), as it does with the flag off."""
    started = await chain_client.post("/api/board/missions/m1/start")
    assert started.status_code == 200
    assert "dependency_state" not in started.json()


async def test_flag_on_move_of_a_mission_with_no_dependencies_keeps_the_old_answer(
    pinned_clock: None, chain_client: AsyncClient
) -> None:
    """m4 has no dependencies and nobody waits on it: the move lands at position 0
    with the plain queue-entry keys and the queue version stamp."""
    await chain_client.post("/api/board/queue/add", json={"mission_id": "m4"})
    moved = await chain_client.post(
        "/api/board/queue/move", json={"mission_id": "m4", "to_position": 0}
    )
    assert moved.status_code == 200
    rows = moved.json()["queue"]
    assert [row["mission_id"] for row in rows] == ["m4", "m1", "m2", "m3"]
    assert [row["position"] for row in rows] == [0, 1, 2, 3]
    assert set(rows[0]) == {
        "position", "mission_id", "enabled", "attempts_per_task", "retry_failed", "note",
    }
    assert moved.json()["version"] == PINNING_STAMP


async def test_flag_off_move_with_stored_dependencies_is_not_guarded(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dependency_client: AsyncClient,
) -> None:
    """The dependency rows exist in the store but the flag is off: /queue/move
    answers the old bytes and writes the queue, with no guard and no refusal."""
    dependency_store.set_dependencies("m2", ["m1"], None)
    dependency_store.update_hermes_config(None, features_json=FLAG_OFF_JSON)
    moved = await dependency_client.post(
        "/api/board/queue/move", json={"mission_id": "m2", "to_position": 0}
    )
    assert moved.status_code == 200
    assert moved.content == (
        b'{"queue":[{"position":0,"mission_id":"m2","enabled":true,"attempts_per_task":null,'
        b'"retry_failed":false,"note":null},{"position":1,"mission_id":"m1","enabled":true,'
        b'"attempts_per_task":null,"retry_failed":false,"note":null}],'
        b'"version":"2026-01-01T00:00:00+00:00"}'
    )
    queue = await dependency_client.get("/api/board/queue")
    assert [row["mission_id"] for row in queue.json()["queue"]] == ["m2", "m1"]


async def test_flag_off_start_with_stored_dependencies_is_not_guarded(
    pinned_clock: None,
    dependency_store: DependencyStore,
    dependency_client: AsyncClient,
) -> None:
    """With the flag off the start route never consults the stored dependency: m2,
    whose row says it waits on m1, starts as before."""
    dependency_store.set_dependencies("m2", ["m1"], None)
    dependency_store.update_hermes_config(None, features_json=FLAG_OFF_JSON)
    started = await dependency_client.post("/api/board/missions/m2/start")
    assert started.status_code == 200
    assert "dependency_state" not in started.json()


async def test_flag_off_move_first_and_last_keep_their_old_answer(
    pinned_clock: None, board_api_client: AsyncClient
) -> None:
    """The flag-off pins: /queue/move, /queue/first and /queue/last still write the
    queue byte for byte, with no dependency check, no refusal and a body field the
    flag ignores (move_dependents)."""
    added = await board_api_client.post(
        "/api/board/queue/add",
        json={"mission_id": "m2", "attempts_per_task": 5.0, "retry_failed": True, "note": "next"},
    )
    assert added.status_code == 200
    moved = await board_api_client.post(
        "/api/board/queue/move",
        json={"mission_id": "m2", "to_position": 0, "move_dependents": True},
    )
    assert moved.status_code == 200
    assert moved.content == (
        b'{"queue":[{"position":0,"mission_id":"m2","enabled":true,"attempts_per_task":5.0,'
        b'"retry_failed":true,"note":"next"},{"position":1,"mission_id":"m1","enabled":true,'
        b'"attempts_per_task":null,"retry_failed":false,"note":null}],'
        b'"version":"2026-01-01T00:00:00+00:00"}'
    )
    first = await board_api_client.post("/api/board/queue/first", json={"mission_id": "m1"})
    assert first.status_code == 200
    assert first.content == EXPECTED_QUEUE_BYTES
    last = await board_api_client.post("/api/board/queue/last", json={"mission_id": "m1"})
    assert last.status_code == 200
    assert [row["mission_id"] for row in last.json()["queue"]] == ["m2", "m1"]


QUEUE_IDS = ["m1", "m2", "m3"]
DEPENDENCY_GRAPH = {"m1": [], "m2": ["m1"], "m3": ["m2"]}


def test_build_dependencies_drops_empty_strings_and_duplicates() -> None:
    assert BD.build_dependencies(None) == []
    assert BD.build_dependencies([]) == []
    assert BD.build_dependencies(["m2", "m1", "m2", "", "   ", "m1"]) == ["m2", "m1"]
    assert BD.build_dependencies(("m1", "m2")) == ["m1", "m2"]
    assert BD.build_dependencies(["", ""]) == []


def test_build_dependencies_keeps_the_self_dependency_for_the_validator() -> None:
    # a self-reference is not dropped: validate_dependencies names it
    assert BD.build_dependencies(["m1", "m1"]) == ["m1"]
    assert BD.build_dependencies(["m1", "m2", "m1"]) == ["m1", "m2"]


def test_build_dependencies_rejects_a_non_list_dependency_value() -> None:
    with pytest.raises(ValueError, match="not a string"):
        BD.build_dependencies("m1")
    with pytest.raises(ValueError, match="must be a list of mission ids"):
        BD.build_dependencies({"m1": "m2"})
    with pytest.raises(ValueError, match="must be a list of mission ids"):
        BD.build_dependencies(42)


def test_build_dependencies_rejects_a_dependency_that_is_not_a_string() -> None:
    with pytest.raises(ValueError, match="must be a mission id string"):
        BD.build_dependencies(["m1", 3])
    with pytest.raises(ValueError, match="must be a mission id string"):
        BD.build_dependencies([None])


def test_find_cycle_returns_a_dependency_cycle_of_length_one() -> None:
    assert BD.find_cycle({"m1": ["m1"]}) == ["m1", "m1"]


def test_find_cycle_returns_a_dependency_cycle_of_length_two() -> None:
    assert BD.find_cycle({"m1": ["m2"], "m2": ["m1"]}) == ["m1", "m2", "m1"]


def test_find_cycle_returns_a_dependency_cycle_of_length_three() -> None:
    assert BD.find_cycle({"m1": ["m2"], "m2": ["m3"], "m3": ["m1"]}) == ["m1", "m2", "m3", "m1"]


def test_find_cycle_returns_none_for_a_dependency_tree() -> None:
    assert BD.find_cycle(DEPENDENCY_GRAPH) is None
    assert BD.find_cycle({}) is None


def test_validate_dependencies_self_dependency_is_named_in_the_message() -> None:
    assert BD.validate_dependencies(
        "m1", ["m1"], QUEUE_IDS, [], DEPENDENCY_GRAPH
    ) == ["mission m1 cannot depend on itself"]


def test_validate_dependencies_cycle_message_names_the_dependency_path() -> None:
    two_cycle = BD.validate_dependencies("m1", ["m2"], QUEUE_IDS, [], DEPENDENCY_GRAPH)
    assert two_cycle == ["the mission dependency closes a cycle: m1 -> m2 -> m1"]
    three_cycle = BD.validate_dependencies("m1", ["m3"], QUEUE_IDS, [], DEPENDENCY_GRAPH)
    assert three_cycle == ["the mission dependency closes a cycle: m1 -> m3 -> m2 -> m1"]
    assert BD.validate_dependencies("m3", [], QUEUE_IDS, [], DEPENDENCY_GRAPH) == []


def test_validate_dependencies_rejects_an_unknown_dependency_id() -> None:
    problems = BD.validate_dependencies("m1", ["ghost"], QUEUE_IDS, [], DEPENDENCY_GRAPH)
    assert problems == [
        "unknown mission dependency ghost: it is neither in the queue nor completed"
    ]
    assert BD.validate_dependencies("m1", ["m4"], QUEUE_IDS, [], DEPENDENCY_GRAPH) == [
        "unknown mission dependency m4: it is neither in the queue nor completed"
    ]


def test_validate_dependencies_accepts_a_dependency_completed_and_out_of_the_queue() -> None:
    # m4 finished earlier and is no longer in the queue: it is still known
    assert BD.validate_dependencies("m1", ["m4"], QUEUE_IDS, ["m4"], DEPENDENCY_GRAPH) == []
    assert BD.find_unknown_dependencies(["m4", "ghost"], QUEUE_IDS, ["m4"]) == ["ghost"]


def test_classify_queue_marks_no_dependency_as_due() -> None:
    entries = [{"position": 0, "mission_id": "m1", "depends_on": []}]
    assert BD.classify_queue(entries, {"m1": "queued"}) == {
        "m1": {"state": "due", "reason": "", "depends_on": []}
    }


def test_classify_queue_marks_unmet_dependency_as_waiting_with_ids_in_the_reason() -> None:
    entries = [{"position": 0, "mission_id": "m1", "depends_on": ["m2", "m3"]}]
    verdicts = BD.classify_queue(entries, {"m2": "queued", "m3": "active"})
    assert verdicts["m1"] == {
        "state": "waiting",
        "reason": "waiting for m2, m3",
        "depends_on": ["m2", "m3"],
    }
    # a dependency with no Live State row is unmet, so the entry still waits
    assert BD.classify_queue(entries, {})["m1"]["reason"] == "waiting for m2, m3"


def test_classify_queue_marks_failed_dependency_as_blocked_with_a_reason() -> None:
    entries = [{"position": 0, "mission_id": "m1", "depends_on": ["m2", "m3"]}]
    verdict = BD.classify_queue(entries, {"m2": "completed", "m3": "failed"})["m1"]
    assert verdict["state"] == "blocked"
    assert verdict["reason"] == "blocked by m3: a dependency failed"
    assert verdict["depends_on"] == ["m2", "m3"]


def test_classify_queue_blocked_dependency_becomes_due_when_completed() -> None:
    entries = [{"position": 0, "mission_id": "m1", "depends_on": ["m2", "m3"]}]
    states: dict[str, str] = {"m2": "completed", "m3": "queued"}
    assert BD.classify_queue(entries, states)["m1"]["state"] == "waiting"
    states["m3"] = "completed"
    due = BD.classify_queue(entries, states)["m1"]
    assert due["state"] == "due"
    assert due["reason"] == ""


def test_classify_queue_blocked_dependency_becomes_due_when_removed() -> None:
    blocked = [{"position": 0, "mission_id": "m1", "depends_on": ["m2"]}]
    assert BD.classify_queue(blocked, {"m2": "failed"})["m1"]["state"] == "blocked"
    # the operator drops the failing dependency from the entry
    unblocked = [{"position": 0, "mission_id": "m1", "depends_on": []}]
    assert BD.classify_queue(unblocked, {"m2": "failed"})["m1"] == {
        "state": "due",
        "reason": "",
        "depends_on": [],
    }


def test_find_first_due_skips_unmet_dependency_and_returns_the_next_due() -> None:
    entries = [
        {"position": 0, "mission_id": "m1", "depends_on": ["m3"]},
        {"position": 1, "mission_id": "m2", "depends_on": []},
        {"position": 2, "mission_id": "m3", "depends_on": []},
    ]
    states = {"m1": "queued", "m2": "queued", "m3": "queued"}
    first_due = BD.find_first_due(entries, states)
    assert first_due is not None
    assert first_due["mission_id"] == "m2"


def test_find_first_due_returns_the_active_mission_before_dependency_checks() -> None:
    entries = [
        {"position": 0, "mission_id": "m1", "depends_on": []},
        {"position": 1, "mission_id": "m2", "depends_on": ["m1"]},
    ]
    first_due = BD.find_first_due(entries, {"m1": "queued", "m2": "active"})
    assert first_due is not None
    assert first_due["mission_id"] == "m2"


def test_find_first_due_returns_none_when_all_dependencies_wait_or_block() -> None:
    entries = [
        {"position": 0, "mission_id": "m1", "depends_on": ["m2"]},
        {"position": 1, "mission_id": "m2", "depends_on": ["m1"]},
    ]
    assert BD.find_first_due(entries, {"m1": "queued", "m2": "failed"}) is None
    assert BD.find_first_due(entries, {"m1": "completed", "m2": "completed"}) is None


MOVE_ENTRIES: list[dict[str, Any]] = [
    {"position": 0, "mission_id": "m1", "depends_on": []},
    {"position": 1, "mission_id": "m2", "depends_on": ["m1"]},
    {"position": 2, "mission_id": "m3", "depends_on": ["m2"]},
]


def test_find_move_problems_refuses_a_move_before_its_dependency() -> None:
    entries = [
        {"position": 0, "mission_id": "m1", "depends_on": []},
        {"position": 1, "mission_id": "m2", "depends_on": ["m1"]},
    ]
    assert BD.find_move_problems(entries, 0, "m2") == [
        "mission m2 cannot move before its dependency m1"
    ]
    assert BD.find_move_blockers(entries, 0, "m2") == ["m1"]
    # the same refusal for a position the store clamps to 0
    assert BD.find_move_problems(entries, -1, "m2") == [
        "mission m2 cannot move before its dependency m1"
    ]


def test_find_move_problems_refuses_a_dependency_moved_after_its_dependents() -> None:
    # the rule reads the stored direct depends_on: m1 is outranked by m2, and m3
    # still sits after m2, so only m2 is named
    assert BD.find_move_problems(MOVE_ENTRIES, 2, "m1") == [
        "mission m1 cannot move after the missions that depend on it: m2"
    ]
    assert BD.find_move_blockers(MOVE_ENTRIES, 2, "m1") == ["m2"]
    assert BD.find_move_problems(MOVE_ENTRIES, 2, "m2") == [
        "mission m2 cannot move after the missions that depend on it: m3"
    ]


def test_find_move_problems_accepts_a_move_that_carries_the_dependents_along() -> None:
    assert BD.find_move_problems(MOVE_ENTRIES, 2, "m1", move_dependents=True) == []
    # the transitive dependent m3 rides with m2, so the chain stays in order
    assert BD.plan_move_order(MOVE_ENTRIES, 2, "m1", move_dependents=True) == ["m1", "m2", "m3"]
    assert BD.plan_move_order(MOVE_ENTRIES, 0, "m1") == ["m1", "m2", "m3"]
    assert BD.plan_move_order(MOVE_ENTRIES, 2, "m1") == ["m2", "m3", "m1"]


def test_find_move_problems_accept_a_sound_move_and_a_move_of_a_mission_with_no_order() -> None:
    assert BD.find_move_problems(MOVE_ENTRIES, 1, "m2") == []  # the move changes nothing
    assert BD.find_move_problems(MOVE_ENTRIES, 0, "m1") == []
    assert BD.find_move_problems(MOVE_ENTRIES, 99, "m1", move_dependents=True) == []
    # an id outside the queue is the store's own KeyError, not a dependency refusal
    assert BD.find_move_problems(MOVE_ENTRIES, 1, "ghost") == []


def test_find_move_problems_names_a_chain_broken_by_the_carried_group() -> None:
    # m1 (0), m3 (1), m2 (2) waits on both: carrying m2 with m1 to position 0
    # leaves m2 before its dependency m3
    entries = [
        {"position": 0, "mission_id": "m1", "depends_on": []},
        {"position": 1, "mission_id": "m3", "depends_on": []},
        {"position": 2, "mission_id": "m2", "depends_on": ["m1", "m3"]},
    ]
    assert BD.find_move_problems(entries, 0, "m1", move_dependents=True) == [
        "moving m1 leaves m2 before its dependency m3"
    ]
    assert BD.plan_move_order(entries, 0, "m1", move_dependents=True) == ["m1", "m2", "m3"]
    assert BD.find_move_blockers(entries, 0, "m1", move_dependents=True) == ["m3"]


def test_find_move_problems_refuses_a_move_before_its_dependency_with_move_dependents() -> None:
    # carrying the dependents along does not lift the rule for the moving mission
    assert BD.find_move_problems(MOVE_ENTRIES, 0, "m2", move_dependents=True) == [
        "mission m2 cannot move before its dependency m1"
    ]
    assert BD.find_move_blockers(MOVE_ENTRIES, 0, "m2", move_dependents=True) == ["m1"]


def test_plan_move_order_lands_a_carried_group_after_unrelated_missions() -> None:
    entries = [
        {"position": 0, "mission_id": "m1", "depends_on": []},
        {"position": 1, "mission_id": "m4", "depends_on": []},
        {"position": 2, "mission_id": "m2", "depends_on": ["m1"]},
    ]
    assert BD.plan_move_order(entries, 1, "m1", move_dependents=True) == ["m4", "m1", "m2"]
    assert BD.find_move_problems(entries, 1, "m1", move_dependents=True) == []
    # the same move without the group leaves m2 before its dependency m1
    assert BD.find_move_problems(entries, 2, "m1") == [
        "mission m1 cannot move after the missions that depend on it: m2"
    ]


def test_plan_move_order_clamps_a_carried_group_to_the_end_of_the_queue() -> None:
    entries = [
        {"position": 0, "mission_id": "m1", "depends_on": []},
        {"position": 1, "mission_id": "m2", "depends_on": ["m1"]},
        {"position": 2, "mission_id": "m3", "depends_on": []},
        {"position": 3, "mission_id": "m4", "depends_on": []},
    ]
    assert BD.plan_move_order(entries, 99, "m1", move_dependents=True) == ["m3", "m4", "m1", "m2"]
    assert BD.find_move_problems(entries, 99, "m1", move_dependents=True) == []


def test_find_move_problems_ignore_a_dependency_already_out_of_the_queue() -> None:
    # m1 is completed and gone from the queue: m2 waiting on it stays unmovable
    # only in the classification, not in the order rule
    entries = [
        {"position": 0, "mission_id": "m2", "depends_on": ["m1"]},
        {"position": 1, "mission_id": "m3", "depends_on": []},
    ]
    assert BD.find_move_problems(entries, 1, "m2", state_by_id={"m1": "completed"}) == []
    assert BD.plan_move_order(entries, 1, "m2", state_by_id={"m1": "completed"}) == ["m3", "m2"]
