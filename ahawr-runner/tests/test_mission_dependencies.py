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

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import ahawr_runner.board_dependencies as BD
from ahawr_runner import board
from ahawr_runner.api import create_app
from tests.test_board_api import board_client

PINNING_STAMP = "2026-01-01T00:00:00+00:00"  # every now() stamp in the fixture lands on this
FLAG_ON_JSON = '{"mission_dependencies": {"enabled": true}}'

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
async def dependency_client(settings) -> AsyncGenerator[AsyncClient, None]:
    """A board app with the ``mission_dependencies`` flag on and two queued
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
    store.upsert_live_state("sk1", None, plan={"tasks": [{"id": "T1", "objective": "one"}]}, status="queued")
    store.upsert_live_state("sk2", None, plan={"tasks": [{"id": "T2", "objective": "two"}]}, status="queued")
    store.update_hermes_config(None, features_json=FLAG_ON_JSON)
    app = create_app(settings)
    app.state.board_store = store
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
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
    saved = await dependency_client.post("/api/board/missions/m2", json={"depends_on": ["m1"], "who": "op"})
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


async def test_flag_off_ignores_a_sent_dependency(
    pinned_clock: None, board_api_client: AsyncClient
) -> None:
    """With the flag off a sent depends_on is dropped like any unknown field: the
    mission answer keeps the pinned bytes and no 400 is raised."""
    answer = await board_api_client.post(
        "/api/board/missions/m1",
        json={"depends_on": ["ghost"], "who": "op", "expected_version": "2026-01-01T00:00:00+00:00"},
    )
    assert answer.status_code == 200
    assert set(answer.json()) == {"mission"}
    mission = await board_api_client.get("/api/board/missions/m1")
    assert mission.content == EXPECTED_MISSION_BYTES


# ---------------------------------------------------------------------------
# the pure dependency rules of board_dependencies: normalize, validate,
# classify, pick
# ---------------------------------------------------------------------------


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
