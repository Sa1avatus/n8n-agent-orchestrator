"""Eligibility codes and the run-next move of ``board_run_mission.py``.

The store is ``InMemoryBoardStore`` (``board.py``); every case seeds the rows
the eligibility reads: ``enabled``, ``retry_failed``, the Live State status and
the ``review_status`` event row.  Nothing here reads a reason text.

The ``run_now`` cases seed the "is a mission running" fact in
``InMemoryOpsStore`` the way the agent publishes it: one ``host_snapshot`` row
with the ``queue.alive`` flag, and count the ``resume`` calls through an
injectable executor.  The ops store of the route is rebuilt at most once per
``STORE_RETRY_SECONDS`` (the bound of ``board_api.live_store``), which the last
route case pins with a counting factory.

The route cases drive ``create_app`` with those same in-memory stores: they pin
that ``/run-next`` and ``/run-now`` answer like an unmounted path while
``run_mission_from_card`` is off, and that with the flag on the queue moves,
the version conflict and the guard refusal reach the client unchanged.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from ahawr_runner import board_api
from ahawr_runner.api import create_app
from ahawr_runner.board import (
    GuardError,
    InMemoryBoardStore,
    LiveState,
    Mission,
    QueueEntry,
    now,
)
from ahawr_runner.board_ops_actions import ACTIONS
from ahawr_runner.board_ops_store import InMemoryOpsStore
from ahawr_runner.board_ops_store_pg import UnavailableOpsStore
from ahawr_runner.board_run_mission import (
    BLOCKED_REASONS,
    BOUNDARY_STOP_ACTION,
    REASON_COMPLETED,
    REASON_DISABLED,
    REASON_FAILED,
    REASON_RUNNING,
    RESUME_ACTION,
    RUN_NEXT_POSITION,
    STATUS_AFTER_RUNNING,
    STATUS_RESUME_QUEUED,
    mission_run_eligibility,
    run_next,
    run_now,
)
from ahawr_runner.config import Settings
from ahawr_runner.dashboard_auth import AuthGuard
from ahawr_runner.dashboard_auth_config import DashboardAuthSettings


def _store(
    status: str | None = None,
    retry_failed: bool = False,
    mission_enabled: bool = True,
    queue_enabled: bool = True,
    stale: bool = False,
    run_id: str | None = "run_a",
) -> InMemoryBoardStore:
    """One mission ``m1`` (state ``sk1``) queued at 0, plus ``m2`` at position 1."""
    store = InMemoryBoardStore()
    store._missions["m1"] = Mission(
        mission_id="m1", state_namespace="sk1", name="Test mission",
        enabled=mission_enabled,
    )
    store._missions["m2"] = Mission(mission_id="m2", state_namespace="sk2", name="Second mission")
    store._queue["m1"] = QueueEntry(
        position=0, mission_id="m1", enabled=queue_enabled, retry_failed=retry_failed,
    )
    store._queue["m2"] = QueueEntry(position=1, mission_id="m2")
    store._queue_order.extend(["m1", "m2"])
    store._live["sk1"] = LiveState(
        state_key="sk1",
        status=status,
        plan={"tasks": [{"id": "T001"}, {"id": "T002"}]},
        task_index=0,
        updated_at="2020-01-01T00:00:00+00:00" if stale else now(),
        worker_run_id=run_id,
    )
    return store


def test_reason_codes_are_the_named_set() -> None:
    assert {REASON_DISABLED, REASON_FAILED, REASON_RUNNING, REASON_COMPLETED} == BLOCKED_REASONS


def test_disabled_mission_is_refused_from_either_row() -> None:
    assert mission_run_eligibility(_store(mission_enabled=False), "m1") == REASON_DISABLED
    queue_row_disabled = _store(status="active", queue_enabled=False)
    assert mission_run_eligibility(queue_row_disabled, "m1") == REASON_DISABLED


def test_running_mission_is_refused() -> None:
    assert mission_run_eligibility(_store(status="active"), "m1") == REASON_RUNNING


def test_completed_mission_is_refused() -> None:
    assert mission_run_eligibility(_store(status="completed"), "m1") == REASON_COMPLETED


def test_failed_mission_needs_the_retry_flag() -> None:
    assert mission_run_eligibility(_store(status="failed"), "m1") == REASON_FAILED
    assert mission_run_eligibility(_store(status="stopped", retry_failed=True), "m1") is None
    assert mission_run_eligibility(_store(status="stopped"), "m1") == REASON_FAILED


def test_queued_or_interrupted_mission_is_allowed() -> None:
    # no Live State row at all: never started
    store = _store()
    del store._live["sk1"]
    assert mission_run_eligibility(store, "m1") is None
    # active but stale (interrupted): the queue resumes it, so it can run next
    assert mission_run_eligibility(_store(status="active", stale=True), "m1") is None


def test_missing_status_falls_back_to_the_stored_review_verdict() -> None:
    store = _store(status=None)
    store._events["sk1"] = [
        {"task_id": "T001", "event_type": "review_parsed", "review_status": "needs_changes"},
    ]
    assert mission_run_eligibility(store, "m1") == REASON_FAILED

    retrying = _store(status=None, retry_failed=True)
    retrying._events["sk1"] = [
        {"task_id": "T001", "event_type": "review_parsed", "review_status": "needs_changes"},
    ]
    assert mission_run_eligibility(retrying, "m1") is None

    passed = _store(status=None)
    passed._events["sk1"] = [
        {"task_id": "T001", "event_type": "review_parsed", "review_status": "pass"},
    ]
    assert mission_run_eligibility(passed, "m1") == REASON_COMPLETED


def test_unknown_mission_raises_key_error() -> None:
    with pytest.raises(KeyError, match="m9"):
        mission_run_eligibility(_store(), "m9")


def test_run_next_moves_to_position_zero_through_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(status="failed", retry_failed=True)
    store._missions["m3"] = Mission(mission_id="m3", state_namespace="sk3", name="Third mission")
    store._queue["m3"] = QueueEntry(position=2, mission_id="m3")
    store._queue_order.append("m3")
    # pin that the move goes through ``store.move_queue`` at position 0, not a
    # raw queue update: the wrapper records and delegates to the real method
    moves: list[tuple[str, int]] = []
    original_move = store.move_queue

    def record_move(
        mission_id: str, to_position: int, expected_version: str | None
    ) -> list[QueueEntry]:
        moves.append((mission_id, to_position))
        return original_move(mission_id, to_position, expected_version)

    monkeypatch.setattr(store, "move_queue", record_move)
    queue = run_next(store, "m3", expected_version=store._queue_version)
    assert moves == [("m3", RUN_NEXT_POSITION)]
    assert [entry.mission_id for entry in queue] == ["m3", "m1", "m2"]
    assert [entry.position for entry in queue] == [0, 1, 2]


def test_run_next_refuses_a_running_mission_with_the_reason_code() -> None:
    store = _store(status="active")
    with pytest.raises(GuardError, match=REASON_RUNNING):
        run_next(store, "m1")
    assert [entry.mission_id for entry in store.queue()] == ["m1", "m2"]


def test_run_next_refuses_a_disabled_mission_with_the_reason_code() -> None:
    store = _store(mission_enabled=False)
    with pytest.raises(GuardError, match=REASON_DISABLED):
        run_next(store, "m1")
    assert [entry.mission_id for entry in store.queue()] == ["m1", "m2"]
    store_queue_row = _store(status="active", queue_enabled=False)
    with pytest.raises(GuardError, match=REASON_DISABLED):
        run_next(store_queue_row, "m1")


def test_run_next_refuses_a_failed_mission_without_retry_failed_with_the_reason_code() -> None:
    store = _store(status="failed")
    with pytest.raises(GuardError, match=REASON_FAILED):
        run_next(store, "m1")
    assert [entry.mission_id for entry in store.queue()] == ["m1", "m2"]
    # with the retry flag the same mission is accepted and reaches position 0
    retry_store = _store(status="failed", retry_failed=True)
    queue = run_next(retry_store, "m1", expected_version=retry_store._queue_version)
    assert queue[0].mission_id == "m1"
    assert [entry.mission_id for entry in retry_store.queue()] == ["m1", "m2"]


def test_run_next_needs_a_queue_row() -> None:
    store = _store()
    del store._queue["m2"]
    store._queue_order.remove("m2")
    with pytest.raises(KeyError, match="is not in the queue"):
        run_next(store, "m2")


def _ops_store(queue_alive: bool) -> InMemoryOpsStore:
    """An ops store whose newest host snapshot reports the queue running or idle."""
    ops = InMemoryOpsStore()
    ops._snapshots = [{"at": now(), "data_json": {"queue": {"alive": queue_alive}}}]
    return ops


class _RecordingExecutor:
    """The injected executor: it records the action calls instead of writing rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action, args))
        return {"status": "queued"}


def test_run_now_moves_first_and_enqueues_resume_once_when_idle() -> None:
    store = _store(status="failed", retry_failed=True)
    ops = _ops_store(queue_alive=False)
    # ``m2`` starts at position 1: the idle answer must sit at position 0
    answer = run_now(store, "m2", ops)
    assert answer["status"] == STATUS_RESUME_QUEUED
    assert answer["action"] == RESUME_ACTION
    assert answer["command"]["action"] == RESUME_ACTION
    assert len(ops.list_commands(action=RESUME_ACTION)) == 1
    queue = store.queue()
    assert [entry.mission_id for entry in queue] == ["m2", "m1"]
    assert queue[0].position == RUN_NEXT_POSITION


def test_run_now_uses_the_injected_executor_exactly_once() -> None:
    store = _store(status="failed", retry_failed=True)
    ops = _ops_store(queue_alive=False)
    executor = _RecordingExecutor()
    answer = run_now(store, "m1", ops, executor=executor)
    assert executor.calls == [(RESUME_ACTION, {})]
    assert answer["status"] == STATUS_RESUME_QUEUED
    assert ops.list_commands() == []


def test_run_now_running_offers_the_boundary_stop_and_wakes_nothing() -> None:
    store = _store(status="failed", retry_failed=True)
    ops = _ops_store(queue_alive=True)
    executor = _RecordingExecutor()
    answer = run_now(store, "m1", ops, executor=executor)
    assert answer == {
        "status": STATUS_AFTER_RUNNING,
        "mission_id": "m1",
        "offer_action": BOUNDARY_STOP_ACTION,
    }
    assert executor.calls == []  # ``resume`` is never called while one runs
    # nothing reaches the ops store: no ``stop_mission`` and no ``pause_at_boundary``
    assert ops.list_commands() == []
    # the offered action is a real entry of the ops catalogue, not a phantom name
    assert BOUNDARY_STOP_ACTION in ACTIONS
    assert [entry.mission_id for entry in store.queue()] == ["m1", "m2"]


def test_run_now_running_still_moves_the_mission_first() -> None:
    store = _store(status="failed", retry_failed=True)
    store._missions["m3"] = Mission(mission_id="m3", state_namespace="sk3", name="Third mission")
    store._queue["m3"] = QueueEntry(position=2, mission_id="m3")
    store._queue_order.append("m3")
    answer = run_now(store, "m3", _ops_store(queue_alive=True))
    assert answer["status"] == STATUS_AFTER_RUNNING
    assert [entry.mission_id for entry in store.queue()] == ["m3", "m1", "m2"]


def test_run_now_refuses_an_ineligible_mission_before_touching_the_queue() -> None:
    store = _store(status="active")
    ops = _ops_store(queue_alive=False)
    with pytest.raises(GuardError, match=REASON_RUNNING):
        run_now(store, "m1", ops)
    assert ops.list_commands() == []


# ------------------------------------------------------------------ the routes of board_api
#: The feature flag the two run routes sit behind (``features_defaults.json``).
RUN_FROM_CARD = "run_mission_from_card"
HEADER = {"X-AHAWR-Dashboard": "1"}
RUN_NEXT_PATH = "/api/board/missions/m2/run-next"
RUN_NOW_PATH = "/api/board/missions/m2/run-now"


def _set_flag(store: InMemoryBoardStore, enabled: bool) -> None:
    """Store the flag the way the settings route stores it (``features_json``)."""
    store.update_hermes_config(
        None, features_json=json.dumps({RUN_FROM_CARD: {"enabled": enabled}})
    )


@pytest.fixture
async def run_client(
    settings: Settings,
) -> AsyncIterator[tuple[AsyncClient, InMemoryBoardStore, InMemoryOpsStore]]:
    """The runner app on the seeded in-memory stores: the board of ``_store()``,
    an ops store reporting the queue idle.  No database, no host agent."""
    store = _store(status="failed", retry_failed=True)
    ops = _ops_store(queue_alive=False)
    app = create_app(settings)
    app.state.board_store = store
    app.state.board_ops_store = ops
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            yield client, store, ops


async def test_run_routes_answer_like_an_unknown_route_with_the_flag_off(
    run_client: tuple[AsyncClient, InMemoryBoardStore, InMemoryOpsStore],
) -> None:
    client, store, _ = run_client
    _set_flag(store, False)
    # an existing board route: its answer stays byte-for-byte what it was
    # before the request, and unchanged by the refused run attempts
    existing = await client.get("/api/board/queue", headers=HEADER)
    assert existing.status_code == 200
    unknown = await client.post("/api/board/missions/m2/not-mounted", headers=HEADER, json={})
    for path in (RUN_NEXT_PATH, RUN_NOW_PATH):
        answer = await client.post(path, headers=HEADER, json={})
        assert answer.status_code == unknown.status_code == 404, path
        assert answer.text == unknown.text, path
        assert answer.headers["content-type"] == unknown.headers["content-type"], path
    after = await client.get("/api/board/queue", headers=HEADER)
    assert after.status_code == existing.status_code == 200
    assert after.text == existing.text
    assert [entry.mission_id for entry in store.queue()] == ["m1", "m2"]


async def test_run_next_moves_the_mission_to_the_front_with_the_flag_on(
    run_client: tuple[AsyncClient, InMemoryBoardStore, InMemoryOpsStore],
) -> None:
    client, store, _ = run_client
    _set_flag(store, True)
    answer = await client.post(RUN_NEXT_PATH, headers=HEADER, json={})
    assert answer.status_code == 200
    body = answer.json()
    assert [entry["mission_id"] for entry in body["queue"]] == ["m2", "m1"]
    assert [entry["position"] for entry in body["queue"]] == [0, 1]
    assert body["version"] == store._queue_version


async def test_run_next_with_a_stale_queue_version_answers_the_conflict(
    run_client: tuple[AsyncClient, InMemoryBoardStore, InMemoryOpsStore],
) -> None:
    client, store, _ = run_client
    _set_flag(store, True)
    answer = await client.post(
        RUN_NEXT_PATH, headers=HEADER, json={"expected_version": "2000-01-01T00:00:00+00:00"}
    )
    assert answer.status_code == 409
    assert answer.json()["error"]["code"] == "conflict"
    assert [entry.mission_id for entry in store.queue()] == ["m1", "m2"]


async def test_run_next_without_the_dashboard_header_is_403(
    run_client: tuple[AsyncClient, InMemoryBoardStore, InMemoryOpsStore],
) -> None:
    client, store, _ = run_client
    _set_flag(store, True)
    answer = await client.post(RUN_NEXT_PATH, json={})
    assert answer.status_code == 403
    assert answer.json()["error"]["message"] == "missing X-AHAWR-Dashboard header"
    assert [entry.mission_id for entry in store.queue()] == ["m1", "m2"]


async def test_run_next_is_403_on_the_read_only_port(settings: Settings) -> None:
    """The read-only guard the dashboard puts on its LAN port (``AuthGuard``)
    answers before the run route runs, with the flag on: the queue never moves.
    If the route were not mounted the answer would be 404, not this 403."""
    store = _store(status="failed", retry_failed=True)
    _set_flag(store, True)
    app = create_app(settings)
    app.state.board_store = store
    app.state.board_ops_store = _ops_store(queue_alive=False)
    app.add_middleware(AuthGuard, settings=DashboardAuthSettings(readonly=True))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            answer = await client.post(RUN_NEXT_PATH, headers=HEADER, json={})
    assert answer.status_code == 403
    assert answer.json()["error"] == "dashboard is in read-only mode"
    assert [entry.mission_id for entry in store.queue()] == ["m1", "m2"]


async def test_run_next_of_a_mission_not_on_the_board_is_404_with_a_reason(
    run_client: tuple[AsyncClient, InMemoryBoardStore, InMemoryOpsStore],
) -> None:
    client, store, _ = run_client
    _set_flag(store, True)
    answer = await client.post("/api/board/missions/m9/run-next", headers=HEADER, json={})
    assert answer.status_code == 404
    assert answer.json()["error"]["code"] == "not_found"
    assert "m9' is not on the board" in answer.json()["error"]["message"]


async def test_run_next_of_a_running_mission_is_refused_with_the_guard_answer(
    run_client: tuple[AsyncClient, InMemoryBoardStore, InMemoryOpsStore],
) -> None:
    client, store, _ = run_client
    _set_flag(store, True)
    store._live["sk1"] = LiveState(
        state_key="sk1", status="active", plan={"tasks": [{"id": "T001"}]},
        task_index=0, updated_at=now(), worker_run_id="run_a",
    )
    answer = await client.post("/api/board/missions/m1/run-next", headers=HEADER, json={})
    assert answer.status_code == 403
    assert answer.json()["error"]["code"] == "guard"
    assert REASON_RUNNING in answer.json()["error"]["message"]
    assert [entry.mission_id for entry in store.queue()] == ["m1", "m2"]


async def test_run_now_enqueues_the_resume_row_once_and_answers_the_status(
    run_client: tuple[AsyncClient, InMemoryBoardStore, InMemoryOpsStore],
) -> None:
    client, store, ops = run_client
    _set_flag(store, True)
    answer = await client.post(RUN_NOW_PATH, headers=HEADER, json={})
    assert answer.status_code == 200
    body = answer.json()
    assert body["status"] == STATUS_RESUME_QUEUED
    assert body["mission_id"] == "m2"
    assert body["action"] == RESUME_ACTION
    assert [entry["mission_id"] for entry in body["queue"]] == ["m2", "m1"]
    assert len(ops.list_commands(action=RESUME_ACTION)) == 1


async def test_run_now_offers_the_boundary_stop_while_a_mission_runs(
    run_client: tuple[AsyncClient, InMemoryBoardStore, InMemoryOpsStore],
) -> None:
    client, store, ops = run_client
    _set_flag(store, True)
    ops._snapshots = [{"at": now(), "data_json": {"queue": {"alive": True}}}]
    answer = await client.post(RUN_NOW_PATH, headers=HEADER, json={})
    assert answer.status_code == 200
    body = answer.json()
    assert body["status"] == STATUS_AFTER_RUNNING
    assert body["offer_action"] == BOUNDARY_STOP_ACTION
    assert ops.list_commands() == []
    assert [entry.mission_id for entry in store.queue()] == ["m2", "m1"]


async def test_run_now_without_the_dashboard_header_is_403(
    run_client: tuple[AsyncClient, InMemoryBoardStore, InMemoryOpsStore],
) -> None:
    """The header guard answers before the queue move, so a "run now" without the
    dashboard header is 403 (not the flag-off 404) and wakes nothing."""
    client, store, ops = run_client
    _set_flag(store, True)
    answer = await client.post(RUN_NOW_PATH, json={})
    assert answer.status_code == 403
    assert answer.json()["error"]["message"] == "missing X-AHAWR-Dashboard header"
    assert [entry.mission_id for entry in store.queue()] == ["m1", "m2"]
    assert ops.list_commands() == []


async def test_run_now_with_a_stale_queue_version_answers_the_conflict(
    run_client: tuple[AsyncClient, InMemoryBoardStore, InMemoryOpsStore],
) -> None:
    """The optimistic lock of the settings/queue version stops the move before the
    ``resume`` row is written, so the stale answer is 409 and the order stays put."""
    client, store, ops = run_client
    _set_flag(store, True)
    answer = await client.post(
        RUN_NOW_PATH, headers=HEADER, json={"expected_version": "2000-01-01T00:00:00+00:00"}
    )
    assert answer.status_code == 409
    assert answer.json()["error"]["code"] == "conflict"
    assert [entry.mission_id for entry in store.queue()] == ["m1", "m2"]
    assert [entry.position for entry in store.queue()] == [0, 1]
    assert ops.list_commands() == []


async def test_run_next_rejects_a_body_with_a_non_string_version(
    run_client: tuple[AsyncClient, InMemoryBoardStore, InMemoryOpsStore],
) -> None:
    client, store, _ = run_client
    _set_flag(store, True)
    answer = await client.post(RUN_NEXT_PATH, headers=HEADER, json={"expected_version": 7})
    assert answer.status_code == 400
    assert "expected_version" in answer.json()["error"]["message"]


async def test_run_now_rebuilds_an_unavailable_ops_store_once_per_window(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ops store of ``run_now`` is rebuilt at most once per
    ``STORE_RETRY_SECONDS``, the same bound as ``board_api.live_store``: the
    second request inside the window reuses the store instead of calling the
    factory again, and the 503 reason is the store's own (never a DSN)."""
    store = _store(status="failed", retry_failed=True)
    _set_flag(store, True)
    app = create_app(settings)
    app.state.board_store = store
    app.state.board_ops_store = UnavailableOpsStore("no ops backend in the test")
    builds = 0

    def rebuilt() -> UnavailableOpsStore:
        nonlocal builds
        builds += 1
        return UnavailableOpsStore(f"ops backend attempt {builds}")

    monkeypatch.setattr(board_api, "get_ops_store", rebuilt)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            first = await client.post(RUN_NOW_PATH, headers=HEADER, json={})
            again = await client.post(RUN_NOW_PATH, headers=HEADER, json={})
            monkeypatch.setattr(board_api, "STORE_RETRY_SECONDS", 0.0)
            monkeypatch.setattr(board_api, "get_ops_store", lambda: _ops_store(queue_alive=False))
            after = await client.post(RUN_NOW_PATH, headers=HEADER, json={})
    assert first.status_code == again.status_code == 503
    assert "attempt 1" in first.text and "attempt 1" in again.text
    assert builds == 1
    assert after.status_code == 200
    assert after.json()["status"] == STATUS_RESUME_QUEUED
    assert len(app.state.board_ops_store.list_commands(action=RESUME_ACTION)) == 1
