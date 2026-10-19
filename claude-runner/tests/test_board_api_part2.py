"""T005 — the one backend gap from ``docs/board-part2-notes.md`` ("Backend
gaps"): the edit route now accepts ``verification`` and ``scope`` in
addition to ``title`` / ``objective`` / ``acceptance_criteria``
(``EDITABLE_TASK_FIELDS``, board_plan.py).  Runs on the in-memory store with
the manager stub from ``create_app``, no database."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from claude_runner.board import InMemoryBoardStore, Mission, QueueEntry


async def _run_lifespan(app) -> None:
    async with app.router.lifespan_context(app):
        pass


@pytest.fixture
async def board_client(settings):
    """A runner app wired to a seeded in-memory board store (no DSN)."""
    store = InMemoryBoardStore()
    store._missions["m1"] = Mission(
        mission_id="m1", state_namespace="sk1", name="Source",
        working_directory="/tmp/x", rules=["r1"],
        acceptance_criteria=["c1"], retrieval_corpora=["corpus"],
        updated_at="2026-10-17T00:00:00+00:00",
    )
    store.set_mission_config("m1", {"required_files": ["/tmp/x/a.md"], "hints": "HINTS"}, None)
    store._queue["m1"] = QueueEntry(position=0, mission_id="m1", enabled=True)
    store._queue_order.append("m1")
    store.upsert_live_state("sk1", None, plan={"tasks": [{"id": "T1", "objective": "one"}]},
                           status="queued")
    from claude_runner.api import create_app
    app = create_app(settings)
    app.state.board_store = store
    await _run_lifespan(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestEditVerificationScope:
    async def _plan_version(self, board_client) -> str:
        return (await board_client.get("/api/board/missions/m1/plan")).json()["version"]

    async def test_waiting_task_accepts_verification_and_scope(self, board_client):
        # with the live row still at T001, T002 is "waiting" and editable
        await board_client.post("/api/board/missions/m1/tasks", json={
            "title": "second", "objective": "two",
        })
        version = await self._plan_version(board_client)
        r = await board_client.post("/api/board/missions/m1/tasks/T002", json={
            "expected_version": version,
            "verification": ["row counts reported"],
            "scope": ["data pipeline"],
        })
        assert r.status_code == 200
        task = r.json()["plan"]["tasks"][1]
        assert task["verification"] == ["row counts reported"]
        assert task["scope"] == ["data pipeline"]
        # the plan edit is audited through the live-state row
        r = await board_client.get("/api/board/changelog")
        entry = next(
            e for e in r.json()["changelog"]
            if e["action"] == "live state upsert" and e["after_json"].get("plan")
        )
        assert entry["after_json"]["plan"]["tasks"][1]["verification"] == ["row counts reported"]

    async def test_running_task_rejects_verification_and_scope(self, board_client):
        # the live task (T001, live row at index 0) is read-only: verification
        # and scope are refused even though they are editable fields
        r = await board_client.post("/api/board/missions/m1/tasks/T001", json={
            "expected_version": await self._plan_version(board_client),
            "verification": ["x"],
        })
        assert r.status_code == 403
        assert r.json()["code"] == "guard"
        r = await board_client.post("/api/board/missions/m1/tasks/T001", json={
            "expected_version": await self._plan_version(board_client),
            "scope": ["y"],
        })
        assert r.status_code == 403

    async def test_hint_is_still_rejected_by_the_edit_route(self, board_client):
        # the Worker hint stays out of the edit route: it is set through the
        # separate hint endpoint
        await board_client.post("/api/board/missions/m1/tasks", json={
            "title": "second", "objective": "two",
        })
        r = await board_client.post("/api/board/missions/m1/tasks/T002", json={
            "expected_version": await self._plan_version(board_client),
            "hint": "use the corpus",
        })
        assert r.status_code == 403
        assert r.json()["code"] == "guard"

    async def test_stale_version_on_verification_edit_is_409(self, board_client):
        await board_client.post("/api/board/missions/m1/tasks", json={
            "title": "second", "objective": "two",
        })
        version = await self._plan_version(board_client)
        r = await board_client.post("/api/board/missions/m1/tasks/T002", json={
            "expected_version": version, "verification": ["a"],
        })
        assert r.status_code == 200
        # the same write again with the old version: the drift is a conflict
        r = await board_client.post("/api/board/missions/m1/tasks/T002", json={
            "expected_version": version, "scope": ["b"],
        })
        assert r.status_code == 409
        assert r.json()["code"] == "conflict"
