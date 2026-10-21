"""The mission-board HTTP routes (claude_runner/board_api.py): optimistic
locking (409 on a version drift), the confirm gate on dangerous actions,
every write landing in board.changelog, and the 503 with no DSN when the
backend is unavailable.  T015 adds the 409/403/503 mapping on the stats and
readiness routes, the start blocked by an error without confirmation, the
rollback success and conflict, and the dangerous values (script tags and
quotes) answered as data with the HTML/JS never interpolating them
unescaped (board.js uses ``esc()`` for every ``innerHTML`` value)."""

from __future__ import annotations

import copy
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from claude_runner import board_api
from claude_runner.board import InMemoryBoardStore, Mission, QueueEntry
from claude_runner.qa_runner import build_verdict


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
        updated_at="2026-01-01T00:00:00+00:00",
    )
    store.set_mission_config("m1", {"required_files": ["/tmp/x/a.md"], "hints": "HINTS"}, None)
    store._queue["m1"] = QueueEntry(position=0, mission_id="m1", enabled=True)
    store._queue_order.append("m1")
    store.upsert_live_state("sk1", None, plan={"tasks": [{"id": "T1", "objective": "one"}]},
                           status="queued")
    # build the app, then swap in the store (the DSN is unset, so the default
    # store is the unavailable stub)
    from claude_runner.api import create_app
    app = create_app(settings)
    app.state.board_store = store

    await _run_lifespan(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestUnavailableStore:
    async def test_status_is_503_with_no_dsn(self, settings):
        """With no DSN the board routes answer 503 and the reason has no DSN."""
        from claude_runner.api import create_app
        app = create_app(settings)
        await _run_lifespan(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/board/status")
            assert r.status_code == 503
            # the reason explains why the store is unavailable but never carries the DSN
            assert r.json()["code"] == "unavailable"
            assert "postgresql://" not in r.json()["reason"]

    async def test_missions_is_503_when_unavailable(self, settings):
        from claude_runner.api import create_app
        app = create_app(settings)
        await _run_lifespan(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/board/missions")
            assert r.status_code == 503
            assert r.json()["code"] == "unavailable"


class TestMissions:
    async def test_list_missions(self, board_client):
        r = await board_client.get("/api/board/missions")
        assert r.status_code == 200
        assert len(r.json()["missions"]) == 1
        assert r.json()["missions"][0]["mission_id"] == "m1"

    async def test_get_mission(self, board_client):
        r = await board_client.get("/api/board/missions/m1")
        assert r.status_code == 200
        body = r.json()
        assert body["mission"]["mission_id"] == "m1"
        assert body["live_state"] is not None
        assert body["live_state"]["state_key"] == "sk1"
        assert body["config"]["required_files"] == ["/tmp/x/a.md"]

    async def test_get_missing_mission_is_404(self, board_client):
        r = await board_client.get("/api/board/missions/nope")
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"

    async def test_mission_view_data_shape(self, settings):
        """GET /api/board/missions/{id} + GET /api/board/queue carry every
        field the mission view form (T020) renders, from the mission row,
        the queue entry and the mission config object."""
        from claude_runner.api import create_app
        from claude_runner.board import InMemoryBoardStore, Mission, QueueEntry

        store = InMemoryBoardStore()
        store._missions["m1"] = Mission(
            mission_id="m1", state_namespace="sk1", name="Source",
            working_directory="/tmp/x", rules=["r1"],
            acceptance_criteria=["c1"], retrieval_corpora=["corpus"],
            updated_at="2026-01-01T00:00:00+00:00",
        )
        store.set_mission_config(
            "m1",
            {"required_files": ["/tmp/x/a.md"], "hints": "HINTS",
             "worker_model": "claude-opus-4-8", "worker_provider": "anthropic",
             "reviewer_model": "claude-sonnet-4-5",
             "reviewer_provider": "anthropic", "max_tasks": 10,
             "worker_think_policy": "on",
             "worker_think_policy_from_task": 0},
            None,
        )
        store._queue["m1"] = QueueEntry(
            position=0, mission_id="m1", enabled=True, attempts_per_task=5,
        )
        store._queue_order.append("m1")
        store.upsert_live_state(
            "sk1", None,
            plan={"tasks": [{"id": "T1", "objective": "one"}]},
            status="queued",
        )
        app = create_app(settings)
        app.state.board_store = store
        await _run_lifespan(app)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            detail = (await c.get("/api/board/missions/m1")).json()
            queue = (await c.get("/api/board/queue")).json()

        # the mission row
        m = detail["mission"]
        for field in ("name", "objective", "working_directory", "enabled",
                      "rules", "acceptance_criteria", "retrieval_corpora"):
            assert field in m
        # the queue entry
        entry = [e for e in queue["queue"] if e["mission_id"] == "m1"]
        assert entry and "attempts_per_task" in entry[0] and \
            "enabled" in entry[0]
        # the mission config object
        cfg = detail["config"]
        for field in ("worker_model", "worker_provider", "reviewer_model",
                      "reviewer_provider", "max_tasks", "worker_think_policy",
                      "worker_think_policy_from_task", "hints",
                      "required_files"):
            assert field in cfg

    async def test_update_mission(self, board_client):
        r = await board_client.get("/api/board/missions/m1")
        expected = r.json()["mission"]["updated_at"]
        r = await board_client.post("/api/board/missions/m1", json={
            "expected_version": expected, "objective": "do it", "who": "me",
        })
        assert r.status_code == 200
        assert r.json()["mission"]["objective"] == "do it"
        # the changelog has the update
        r = await board_client.get("/api/board/changelog")
        actions = [e["action"] for e in r.json()["changelog"]]
        assert "mission update" in actions

    async def test_update_mission_version_drift_is_409(self, board_client):
        r = await board_client.get("/api/board/missions/m1")
        expected = r.json()["mission"]["updated_at"]
        # bump the version so the stored version no longer matches
        await board_client.post("/api/board/missions/m1", json={
            "expected_version": expected, "objective": "v2",
        })
        r = await board_client.post("/api/board/missions/m1", json={
            "expected_version": expected, "objective": "v3",
        })
        assert r.status_code == 409
        assert r.json()["code"] == "conflict"

    async def test_delete_mission(self, board_client):
        r = await board_client.delete("/api/board/missions/m1")
        assert r.status_code == 200
        assert r.json()["deleted"] == "m1"
        r = await board_client.get("/api/board/missions")
        assert r.json()["missions"] == []

    async def test_delete_running_mission_needs_confirm(self, board_client):
        await board_client.post("/api/board/live-states/sk1", json={
            "expected_version": (await board_client.get("/api/board/live-states/sk1")).json()["live_state"]["updated_at"],
            "status": "active",
        })
        r = await board_client.delete("/api/board/missions/m1")
        assert r.status_code == 403
        assert r.json()["code"] == "guard"
        r = await board_client.delete("/api/board/missions/m1", params={"confirm": "true"})
        assert r.status_code == 200

    async def test_create_mission(self, board_client):
        r = await board_client.post("/api/board/missions/create", json={
            "mission_id": "m2", "source_mission_id": "m1",
            "objective": "do it", "plan": {"tasks": [{"id": "T1"}]},
        })
        assert r.status_code == 200
        assert r.json()["mission"]["mission_id"] == "m2"
        r = await board_client.get("/api/board/missions")
        assert [m["mission_id"] for m in r.json()["missions"]] == ["m1", "m2"]


class TestLiveStates:
    async def test_list_live_states(self, board_client):
        r = await board_client.get("/api/board/live-states")
        assert r.status_code == 200
        assert len(r.json()["live_states"]) == 1

    async def test_get_live_state(self, board_client):
        r = await board_client.get("/api/board/live-states/sk1")
        assert r.status_code == 200
        assert r.json()["live_state"]["state_key"] == "sk1"

    async def test_get_missing_live_state_is_404(self, board_client):
        r = await board_client.get("/api/board/live-states/nope")
        assert r.status_code == 404

    async def test_upsert_live_state(self, board_client):
        r = await board_client.get("/api/board/live-states/sk1")
        expected = r.json()["live_state"]["updated_at"]
        r = await board_client.post("/api/board/live-states/sk1", json={
            "expected_version": expected, "status": "active",
        })
        assert r.status_code == 200
        assert r.json()["live_state"]["status"] == "active"


class TestPlan:
    async def test_get_plan(self, board_client):
        r = await board_client.get("/api/board/missions/m1/plan")
        assert r.status_code == 200
        assert r.json()["plan"]["tasks"][0]["id"] == "T1"

    async def test_add_task(self, board_client):
        r = await board_client.post("/api/board/missions/m1/tasks", json={
            "title": "second", "objective": "two",
        })
        assert r.status_code == 200
        assert len(r.json()["plan"]["tasks"]) == 2

    async def test_update_task(self, board_client):
        # add a second task: with the live row still at T001 it is "waiting"
        # and editable, while T001 (the live task) is running and read-only
        r = await board_client.post("/api/board/missions/m1/tasks", json={
            "title": "second", "objective": "two",
        })
        assert r.status_code == 200
        r = await board_client.post("/api/board/missions/m1/tasks/T002", json={
            "expected_version": (await board_client.get("/api/board/missions/m1/plan"))
            .json()["version"],
            "objective": "edited",
        })
        assert r.status_code == 200
        assert r.json()["plan"]["tasks"][1]["objective"] == "edited"

    async def test_delete_task(self, board_client):
        r = await board_client.delete("/api/board/missions/m1/tasks/T1")
        assert r.status_code == 200
        assert r.json()["plan"]["tasks"] == []

    async def test_retry_task(self, board_client):
        # put T1 into the review state: add a second task (the live task stays
        # T001) and let the review fail it, so T001 becomes not_accepted
        await board_client.post("/api/board/missions/m1/tasks", json={
            "title": "second", "objective": "two",
        })
        live = (await board_client.get("/api/board/live-states/sk1")).json()["live_state"]
        await board_client.post("/api/board/live-states/sk1", json={
            "expected_version": live["updated_at"],
            "status": "active", "task_index": 1, "task_attempt": 3,
        })
        await board_client.post("/api/board/missions/m1/preflight", json={
            "items": [{"task_id": "T001", "event_type": "review_parsed",
                       "review_status": "needs_changes", "review_score": 1}],
        })
        r = await board_client.post("/api/board/missions/m1/tasks/T001/retry", json={})
        assert r.status_code == 200
        assert r.json()["changelog"]["live_state"]["task_attempt"] == 1


class TestReadiness:
    async def test_readiness_endpoint(self, board_client):
        r = await board_client.get("/api/board/missions/m1/readiness")
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert any(i["check"] == "working_folder" for i in body["items"])


class TestStartStopDisable:
    async def test_start_guard(self, board_client):
        r = await board_client.post("/api/board/missions/m1/start")
        assert r.status_code == 200
        assert "allowed" in r.json()

    async def test_stop_running_needs_confirm(self, board_client):
        await board_client.post("/api/board/live-states/sk1", json={
            "expected_version": (await board_client.get("/api/board/live-states/sk1")).json()["live_state"]["updated_at"],
            "status": "active",
        })
        r = await board_client.post("/api/board/missions/m1/stop")
        assert r.status_code == 403
        assert r.json()["code"] == "guard"
        r = await board_client.post("/api/board/missions/m1/stop", params={"confirm": "true"})
        assert r.status_code == 200
        assert r.json()["status"] == "stopped"

    async def test_disable_running_needs_confirm(self, board_client):
        await board_client.post("/api/board/live-states/sk1", json={
            "expected_version": (await board_client.get("/api/board/live-states/sk1")).json()["live_state"]["updated_at"],
            "status": "active",
        })
        r = await board_client.post("/api/board/missions/m1/disable")
        assert r.status_code == 403
        r = await board_client.post("/api/board/missions/m1/disable", params={"confirm": "true"})
        assert r.status_code == 200
        assert r.json()["enabled"] is False


class TestChangelog:
    async def test_changelog_endpoint(self, board_client):
        r = await board_client.get("/api/board/changelog")
        assert r.status_code == 200
        assert "changelog" in r.json()


class TestRollback:
    async def test_rollback(self, board_client):
        # make a change, find its changelog id, rollback
        r = await board_client.get("/api/board/missions/m1")
        expected = r.json()["mission"]["updated_at"]
        await board_client.post("/api/board/missions/m1", json={
            "expected_version": expected, "objective": "changed",
        })
        r = await board_client.get("/api/board/changelog")
        entry_id = next(
            e["id"] for e in r.json()["changelog"]
            if e["action"] == "mission update" and e["mission_id"] == "m1"
        )
        r = await board_client.post(f"/api/board/changelog/{entry_id}/rollback")
        assert r.status_code == 200
        assert r.json()["restored"]["objective"] == ""


class TestTemplates:
    async def test_list_templates(self, board_client):
        r = await board_client.get("/api/board/templates")
        assert r.status_code == 200
        assert r.json()["templates"] == []

    async def test_save_template(self, board_client):
        r = await board_client.post("/api/board/templates/base", json={"mission_id": "m1"})
        assert r.status_code == 200
        assert r.json()["template"]["name"] == "base"

    async def test_delete_template(self, board_client):
        await board_client.post("/api/board/templates/base", json={"mission_id": "m1"})
        r = await board_client.delete("/api/board/templates/base")
        assert r.status_code == 200
        r = await board_client.get("/api/board/templates")
        assert r.json()["templates"] == []


class TestQueue:
    async def test_get_queue(self, board_client):
        r = await board_client.get("/api/board/queue")
        assert r.status_code == 200
        assert [e["mission_id"] for e in r.json()["queue"]] == ["m1"]

    async def test_add_queue(self, board_client):
        r = await board_client.post("/api/board/queue/add", json={"mission_id": "m2"})
        assert r.status_code == 200
        assert [e["mission_id"] for e in r.json()["queue"]] == ["m1", "m2"]

    async def test_remove_queue(self, board_client):
        r = await board_client.delete("/api/board/queue/m1")
        assert r.status_code == 200
        assert r.json()["queue"] == []


class TestFlags:
    async def test_get_flags(self, board_client):
        r = await board_client.get("/api/board/flags")
        assert r.status_code == 200
        assert r.json()["flags"] == {}

    async def test_set_flag(self, board_client):
        r = await board_client.post("/api/board/flags/test", json={"value": "1"})
        assert r.status_code == 200
        assert r.json()["value"] == "1"

    async def test_remove_flag(self, board_client):
        await board_client.post("/api/board/flags/test", json={"value": "1"})
        r = await board_client.delete("/api/board/flags/test")
        assert r.status_code == 200


class TestPreflight:
    async def test_get_preflight(self, board_client):
        r = await board_client.get("/api/board/missions/m1/preflight")
        assert r.status_code == 200

    async def test_record_preflight(self, board_client):
        r = await board_client.post("/api/board/missions/m1/preflight", json={
            "items": [{"check": "ok"}],
        })
        assert r.status_code == 200
        assert r.json()["preflight"] is not None


class TestConfig:
    async def test_get_config(self, board_client):
        r = await board_client.get("/api/board/missions/m1/config")
        assert r.status_code == 200
        assert r.json()["config"]["required_files"] == ["/tmp/x/a.md"]

    async def test_update_config(self, board_client):
        r = await board_client.get("/api/board/missions/m1/config")
        expected = r.json()["version"]
        r = await board_client.post("/api/board/missions/m1/config", json={
            "expected_version": expected, "hints": "NEW HINTS",
        })
        assert r.status_code == 200
        assert r.json()["config"]["hints"] == "NEW HINTS"


class TestThinking:
    async def test_policy_check(self, board_client):
        r = await board_client.post("/api/board/thinking/policy", json={
            "policy": {"variants": [{"name": "always", "first": [["think", None]]}]},
        })
        assert r.status_code == 200
        assert r.json()["valid"] is True

    async def test_variant(self, board_client):
        r = await board_client.post("/api/board/thinking/variant", json={
            "policy": {"variants": [{"name": "always", "first": [["think", None]]}], "from_task": 1},
            "task_number": 2,
        })
        assert r.status_code == 200
        assert r.json()["variant"] == "always"

    async def test_layout(self, board_client):
        r = await board_client.post("/api/board/thinking/layout", json={
            "policy": {"variants": [{"name": "always", "first": [["think", None]]}]}, "n": 4,
        })
        assert r.status_code == 200
        assert len(r.json()["layout"]) == 4

    async def test_stats(self, board_client):
        r = await board_client.post("/api/board/thinking/stats", json={
            "attempts": [{"variant": "a", "pass": 1, "secs": 100}],
        })
        assert r.status_code == 200
        assert len(r.json()["variants"]) == 1

    async def test_thompson(self, board_client):
        r = await board_client.post("/api/board/thinking/thompson", json={
            "attempts": [{"variant": "a", "pass": 1, "secs": 100}], "samples": 100,
        })
        assert r.status_code == 200
        assert len(r.json()["thompson"]) == 1


class TestStats:
    async def test_mission_stats(self, board_client):
        r = await board_client.get("/api/board/missions/m1/stats")
        assert r.status_code == 200

    async def test_task_stats(self, board_client):
        r = await board_client.get("/api/board/missions/m1/tasks/T1/stats")
        assert r.status_code == 200

    async def test_stats_of_a_missing_mission_are_404(self, board_client):
        r = await board_client.get("/api/board/missions/nope/stats")
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"

    async def test_task_stats_of_a_missing_task_are_404(self, board_client):
        r = await board_client.get("/api/board/missions/m1/tasks/T9/stats")
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"


class TestStartBlockedOnError:
    """The start is refused (allowed=false, blocked_by the error items) while
    an error check stands and no confirmation is passed; the confirmation
    lets it through (allowed=true)."""

    async def test_start_blocked_by_an_error_without_confirm(self, board_client):
        # drop the working folder so the readiness gets an error item
        await board_client.post("/api/board/missions/m1", json={
            "expected_version": (await board_client.get("/api/board/missions/m1"))
            .json()["mission"]["updated_at"],
            "working_directory": "/no/such/folder",
        })
        r = await board_client.post("/api/board/missions/m1/start")
        assert r.status_code == 200
        assert r.json()["allowed"] is False
        assert "working_folder" in r.json()["reason"]
        # with confirmed=true the start goes through
        r = await board_client.post("/api/board/missions/m1/start",
                                   params={"confirmed": "true"})
        assert r.status_code == 200
        assert r.json()["allowed"] is True
        assert r.json()["confirmed"] is True
        assert r.json()["blocked_by"] == []


class TestRollbackApi:
    async def test_rollback_success_and_backup_row(self, board_client):
        # make a change and find its changelog entry
        r = await board_client.get("/api/board/missions/m1")
        expected = r.json()["mission"]["updated_at"]
        await board_client.post("/api/board/missions/m1", json={
            "expected_version": expected, "objective": "changed",
        })
        r = await board_client.get("/api/board/changelog")
        entry = next(
            e for e in r.json()["changelog"]
            if e["action"] == "mission update" and e["mission_id"] == "m1"
        )
        # the backup row was saved before the change
        assert entry["before_json"]["objective"] == ""
        assert entry["after_json"]["objective"] == "changed"
        r = await board_client.post(f"/api/board/changelog/{entry['id']}/rollback")
        assert r.status_code == 200
        assert r.json()["restored"]["objective"] == ""
        r = await board_client.get("/api/board/missions/m1")
        assert r.json()["mission"]["objective"] == ""

    async def test_rollback_conflicts_when_the_row_changed_after_the_entry(
        self, board_client
    ):
        r = await board_client.get("/api/board/missions/m1")
        expected = r.json()["mission"]["updated_at"]
        await board_client.post("/api/board/missions/m1", json={
            "expected_version": expected, "objective": "changed",
        })
        r = await board_client.get("/api/board/changelog")
        entry = next(
            e for e in r.json()["changelog"]
            if e["action"] == "mission update" and e["mission_id"] == "m1"
        )
        # a second change after the entry makes the row drift from the
        # entry's after_json: the rollback is a 409
        r = await board_client.post("/api/board/missions/m1", json={
            "expected_version": (await board_client.get("/api/board/missions/m1"))
            .json()["mission"]["updated_at"],
            "objective": "changed again",
        })
        assert r.status_code == 200
        r = await board_client.post(f"/api/board/changelog/{entry['id']}/rollback")
        assert r.status_code == 409
        assert r.json()["code"] == "conflict"
        assert r.json()["status"] == "failed"

    async def test_rollback_of_an_unknown_entry_is_404(self, board_client):
        r = await board_client.post("/api/board/changelog/9999/rollback")
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"


class TestDangerousValues:
    """Dangerous values (script tags, quotes) are answered as data: the
    endpoints echo them back exactly, and the page's script never
    interpolates an unescaped value into ``innerHTML`` (the only ``esc``
    in the page escapes ``& < > " '`` and every interpolated value goes
    through it)."""

    async def test_dangerous_values_are_returned_as_data(self, board_client):
        evil = "<script>alert('x')</script>"
        r = await board_client.post("/api/board/missions/m1", json={
            "expected_version": (await board_client.get("/api/board/missions/m1"))
            .json()["mission"]["updated_at"],
            "objective": evil,
        })
        assert r.status_code == 200
        assert r.json()["mission"]["objective"] == evil
        r = await board_client.get("/api/board/missions/m1")
        assert r.json()["mission"]["objective"] == evil
        # the JSON body carries the value verbatim: it is data, not markup
        body = r.content.decode("utf-8")
        assert evil in body
        # the raw markup is not escaped away by the transport either
        assert "&lt;script&gt;" not in body

    async def test_dangerous_objective_is_in_the_changelog_as_data(
        self, board_client
    ):
        evil = '<img src=x onerror="alert(1)"'
        r = await board_client.post("/api/board/missions/m1", json={
            "expected_version": (await board_client.get("/api/board/missions/m1"))
            .json()["mission"]["updated_at"],
            "objective": evil,
        })
        assert r.status_code == 200
        r = await board_client.get("/api/board/changelog")
        entry = next(
            e for e in r.json()["changelog"]
            if e["action"] == "mission update" and e["mission_id"] == "m1"
        )
        assert entry["after_json"]["objective"] == evil

    def test_page_script_escapes_every_innerhtml_value(self):
        """Static check: the page's script escapes every value it writes into
        ``innerHTML`` through ``esc()`` (the page is dashboard.html; the board
        routes serve the same page).  A value like ``<script>`` therefore
        reaches the page as text, never as markup."""
        from pathlib import Path

        from claude_runner import __file__ as module_file

        text = Path(module_file).with_name("dashboard.html").read_text(encoding="utf-8")
        # the esc() definition exists and escapes all five dangerous characters
        assert "const esc = (s) =>" in text
        assert 'String(s ?? "")' in text
        for entity in ("&lt;", "&quot;", "&#39;", "&gt;", "&amp;"):
            assert entity in text
        # every innerHTML write that carries data (a ${...} interpolation) must
        # pass its values through esc(); the only writes without a value are
        # the static reset (box.innerHTML = "") and fixed markup.
        unescaped = [
            line.strip()
            for line in text.splitlines()
            if ".innerHTML" in line and "${" in line and "esc(" not in line
        ]
        assert not unescaped, (
            "innerHTML values are not escaped through esc(): " + repr(unescaped)
        )


class TestConfirmGate:
    """Every dangerous action is refused with 403 while the mission is
    running and no ``confirm=true`` is passed; the confirmation lets the
    action through and the change lands in the changelog."""

    async def _make_running(self, board_client) -> None:
        await board_client.post("/api/board/live-states/sk1", json={
            "expected_version": (await board_client.get("/api/board/live-states/sk1"))
            .json()["live_state"]["updated_at"],
            "status": "active",
        })

    async def test_delete_running_mission_without_confirm_is_403(self, board_client):
        await self._make_running(board_client)
        r = await board_client.delete("/api/board/missions/m1")
        assert r.status_code == 403
        assert r.json()["code"] == "guard"

    async def test_delete_running_mission_with_confirm_is_200_and_audited(
        self, board_client
    ):
        await self._make_running(board_client)
        r = await board_client.delete("/api/board/missions/m1", params={"confirm": "true"})
        assert r.status_code == 200
        r = await board_client.get("/api/board/changelog")
        assert any(
            e["action"] == "mission delete" and e["mission_id"] == "m1"
            for e in r.json()["changelog"]
        )

    async def test_stop_running_mission_without_confirm_is_403(self, board_client):
        await self._make_running(board_client)
        r = await board_client.post("/api/board/missions/m1/stop")
        assert r.status_code == 403
        assert r.json()["code"] == "guard"

    async def test_stop_running_mission_with_confirm_is_200_and_audited(
        self, board_client
    ):
        await self._make_running(board_client)
        r = await board_client.post("/api/board/missions/m1/stop", params={"confirm": "true"})
        assert r.status_code == 200
        r = await board_client.get("/api/board/changelog")
        assert any(
            e["action"] == "live state upsert"
            and e["after_json"].get("status") == "stopped"
            for e in r.json()["changelog"]
        )

    async def test_disable_running_mission_without_confirm_is_403(self, board_client):
        await self._make_running(board_client)
        r = await board_client.post("/api/board/missions/m1/disable")
        assert r.status_code == 403
        assert r.json()["code"] == "guard"

    async def test_disable_running_mission_with_confirm_is_200_and_audited(
        self, board_client
    ):
        await self._make_running(board_client)
        r = await board_client.post("/api/board/missions/m1/disable", params={"confirm": "true"})
        assert r.status_code == 200
        assert r.json()["enabled"] is False
        r = await board_client.get("/api/board/changelog")
        assert any(
            e["action"] == "mission update"
            and e["after_json"].get("enabled") is False
            for e in r.json()["changelog"]
        )

    async def test_remove_queue_running_mission_without_confirm_is_403(self, board_client):
        await self._make_running(board_client)
        r = await board_client.delete("/api/board/queue/m1")
        assert r.status_code == 403
        assert r.json()["code"] == "guard"

    async def test_remove_queue_running_mission_with_confirm_is_200_and_audited(
        self, board_client
    ):
        await self._make_running(board_client)
        r = await board_client.delete("/api/board/queue/m1", params={"confirm": "true"})
        assert r.status_code == 200
        r = await board_client.get("/api/board/changelog")
        assert any(e["action"] == "queue remove" for e in r.json()["changelog"])


class TestStaleVersion409:
    """A version drift is a 409 with code ``conflict``: after a successful
    write a second write with the old version must be refused.  The 409 must
    hold even when both writes land in the same wall second (the row stamps
    must be distinct, otherwise the optimistic lock cannot see the drift)."""

    async def test_stale_version_is_409_on_config(self, board_client):
        expected = (await board_client.get("/api/board/missions/m1/config")).json()["version"]
        r = await board_client.post("/api/board/missions/m1/config", json={
            "expected_version": expected, "hints": "first",
        })
        assert r.status_code == 200
        # a second write with the old version: the version drift is a conflict
        r = await board_client.post("/api/board/missions/m1/config", json={
            "expected_version": expected, "hints": "second",
        })
        assert r.status_code == 409
        assert r.json()["code"] == "conflict"

    async def test_stale_version_is_409_on_live_state(self, board_client):
        expected = (await board_client.get("/api/board/live-states/sk1")).json()["live_state"]["updated_at"]
        r = await board_client.post("/api/board/live-states/sk1", json={
            "expected_version": expected, "status": "active",
        })
        assert r.status_code == 200
        # the same write again with the old version: the drift is a conflict
        # (both writes land in the same second — the stamps must still differ)
        r = await board_client.post("/api/board/live-states/sk1", json={
            "expected_version": expected, "status": "stopped",
        })
        assert r.status_code == 409
        assert r.json()["code"] == "conflict"

    async def test_stale_version_is_409_on_task_update(self, board_client):
        await board_client.post("/api/board/missions/m1/tasks", json={
            "title": "second", "objective": "two",
        })
        expected = (await board_client.get("/api/board/missions/m1/plan")).json()["version"]
        r = await board_client.post("/api/board/missions/m1/tasks/T002", json={
            "expected_version": expected, "objective": "edited once",
        })
        assert r.status_code == 200
        r = await board_client.post("/api/board/missions/m1/tasks/T002", json={
            "expected_version": expected, "objective": "edited twice",
        })
        assert r.status_code == 409
        assert r.json()["code"] == "conflict"


class Test422Validation:
    """The 400 (422-class) mapping for a request body that is malformed (a
    value the schema rejects or a field that carries no usable value).
    The board answers with a readable message: a named ``code`` and an
    ``error`` message, never a bare 500."""

    async def test_invalid_policy_body_answers_with_the_problems(self, board_client):
        # the policy check is a validation endpoint: a malformed body (a
        # policy without a usable variants list) is answered as data with
        # valid=false and the named problems — never a bare 500, and never
        # a 409/403/503 (the board keeps its own error codes for those)
        r = await board_client.post("/api/board/thinking/policy", json={
            "policy": {"variants": []},
        })
        assert r.status_code == 200
        assert r.json()["valid"] is False
        assert "variants" in r.json()["problems"][0]

    async def test_mission_id_with_space_and_slash_is_400_on_create(self, board_client):
        # a space and a slash make the id an unusable field on the create body
        r = await board_client.post("/api/board/missions/create", json={
            "mission_id": "bad id/slash", "objective": "x",
        })
        assert r.status_code in (400, 422)
        assert r.json()["code"] == "bad_mission_id"
        assert "not a valid slug" in r.json()["error"]["message"]


class TestChangelogAudit:
    """Every write endpoint leaves a row in ``board.changelog`` (the before
    row is saved first, so the action and the changed field are visible)."""

    async def _actions(self, board_client) -> list[str]:
        r = await board_client.get("/api/board/changelog")
        return [e["action"] for e in r.json()["changelog"]]

    async def test_update_mission_writes_a_changelog_row(self, board_client):
        r = await board_client.get("/api/board/missions/m1")
        expected = r.json()["mission"]["updated_at"]
        r = await board_client.post("/api/board/missions/m1", json={
            "expected_version": expected, "objective": "audited",
        })
        assert r.status_code == 200
        r = await board_client.get("/api/board/changelog")
        entry = next(
            e for e in r.json()["changelog"]
            if e["action"] == "mission update" and e["mission_id"] == "m1"
        )
        assert entry["after_json"]["objective"] == "audited"

    async def test_create_mission_writes_a_changelog_row(self, board_client):
        r = await board_client.post("/api/board/missions/create", json={
            "mission_id": "m2", "source_mission_id": "m1",
            "objective": "new mission",
        })
        assert r.status_code == 200
        r = await board_client.get("/api/board/changelog")
        assert any(
            e["action"] == "mission create" and e["mission_id"] == "m2"
            for e in r.json()["changelog"]
        )

    async def test_upsert_live_state_writes_a_changelog_row(self, board_client):
        r = await board_client.get("/api/board/live-states/sk1")
        expected = r.json()["live_state"]["updated_at"]
        r = await board_client.post("/api/board/live-states/sk1", json={
            "expected_version": expected, "status": "active",
        })
        assert r.status_code == 200
        r = await board_client.get("/api/board/changelog")
        assert any(
            e["action"] == "live state upsert"
            and e["after_json"]["status"] == "active"
            for e in r.json()["changelog"]
        )

    async def test_add_task_writes_a_changelog_row(self, board_client):
        r = await board_client.post("/api/board/missions/m1/tasks", json={
            "title": "audited", "objective": "two",
        })
        assert r.status_code == 200
        # the plan edit is audited through the live-state write that carries the
        # new plan
        r = await board_client.get("/api/board/changelog")
        entries = [
            e for e in r.json()["changelog"]
            if e["action"] == "live state upsert"
            and (e["after_json"] or {}).get("plan")
            and len((e["after_json"]["plan"] or {}).get("tasks") or []) == 2
        ]
        assert entries
        assert any(
            t.get("title") == "audited" for t in entries[0]["after_json"]["plan"]["tasks"]
        )

    async def test_update_task_writes_a_changelog_row(self, board_client):
        # add a second task: with the live row still at T001 it is "waiting"
        # and editable, while T001 (the live task) is running and read-only
        await board_client.post("/api/board/missions/m1/tasks", json={
            "title": "second", "objective": "two",
        })
        expected = (await board_client.get("/api/board/missions/m1/plan")).json()["version"]
        r = await board_client.post("/api/board/missions/m1/tasks/T002", json={
            "expected_version": expected, "objective": "edited",
        })
        assert r.status_code == 200
        # the plan edit is audited through the live-state write that carries
        # the new plan
        r = await board_client.get("/api/board/changelog")
        entry = next(
            e for e in r.json()["changelog"]
            if e["action"] == "live state upsert"
            and e["after_json"].get("plan")
        )
        assert entry["after_json"]["plan"]["tasks"][1]["objective"] == "edited"

    async def test_set_task_hint_writes_a_changelog_row(self, board_client):
        # add a second task: with the live row still at T001 it is "waiting"
        # and the hint is settable, while T001 (the live task) is running and
        # read-only
        await board_client.post("/api/board/missions/m1/tasks", json={
            "title": "second", "objective": "two",
        })
        expected = (await board_client.get("/api/board/missions/m1/plan")).json()["version"]
        r = await board_client.post("/api/board/missions/m1/tasks/T002/hint", json={
            "expected_version": expected, "hint": "audited hint",
        })
        assert r.status_code == 200
        # the hint lives in the task override, audited as its own row
        r = await board_client.get("/api/board/changelog")
        entry = next(
            e for e in r.json()["changelog"]
            if e["action"] == "task_override set" and e["task_key"] == "T002"
        )
        assert entry["after_json"]["hint"] == "audited hint"

    async def test_update_config_writes_a_changelog_row(self, board_client):
        expected = (await board_client.get("/api/board/missions/m1/config")).json()["version"]
        r = await board_client.post("/api/board/missions/m1/config", json={
            "expected_version": expected, "hints": "audited hints",
        })
        assert r.status_code == 200
        # the config edit is audited through the config row's own audit
        r = await board_client.get("/api/board/changelog")
        entry = next(
            e for e in r.json()["changelog"]
            if e["action"] == "mission_config set" and e["mission_id"] == "m1"
        )
        assert entry["after_json"]["config_json"]["hints"] == "audited hints"

    async def test_delete_task_writes_a_changelog_row(self, board_client):
        r = await board_client.delete("/api/board/missions/m1/tasks/T1")
        assert r.status_code == 200
        # the plan edit is audited through the live-state write that carries
        # the new plan
        r = await board_client.get("/api/board/changelog")
        entry = next(
            e for e in r.json()["changelog"]
            if e["action"] == "live state upsert"
            and e["after_json"].get("plan")
        )
        assert entry["after_json"]["plan"]["tasks"] == []

    async def test_remove_queue_writes_a_changelog_row(self, board_client):
        r = await board_client.delete("/api/board/queue/m1")
        assert r.status_code == 200
        r = await board_client.get("/api/board/changelog")
        assert any(e["action"] == "queue remove" for e in r.json()["changelog"])


class TestChangelogCoverage:
    """Every write endpoint of the board router writes a ``board.changelog``
    row on success: the row count grows by exactly the expected amount after
    each write (create and delete each write two rows; every other write one)."""

    async def test_every_write_endpoint_writes_a_changelog_row(self, board_client):
        async def count() -> int:
            return len((await board_client.get("/api/board/changelog")).json()["changelog"])

        baseline = await count()

        # mission create: the mission row and the queue snapshot are two rows
        await board_client.post("/api/board/missions/create", json={
            "mission_id": "m2", "source_mission_id": "m1", "objective": "new",
        })
        assert await count() == baseline + 2

        # mission delete: the mission row and the queue snapshot are two rows
        # (delete m1 while m2 is still there: m1 is not running, no confirm needed)
        await board_client.delete("/api/board/missions/m1")
        assert await count() == baseline + 4

        # a plan edit (adding a task) is audited through the live-state row
        await board_client.post("/api/board/missions/m2/tasks", json={
            "title": "added", "objective": "two",
        })
        assert await count() == baseline + 5

        # the plan must then hold a second, waiting task for the task edit and
        # delete below to be valid (a fresh mission's only task is the live
        # one and read-only)
        await board_client.post("/api/board/missions/m2/tasks", json={
            "title": "second", "objective": "two",
        })
        assert await count() == baseline + 6

        # a task edit is audited through the live-state row too
        expected = (await board_client.get("/api/board/missions/m2/plan")).json()["version"]
        await board_client.post("/api/board/missions/m2/tasks/T002", json={
            "expected_version": expected, "objective": "edited",
        })
        assert await count() == baseline + 7

        # deleting a task is a live-state write (the new plan)
        await board_client.delete("/api/board/missions/m2/tasks/T002")
        assert await count() == baseline + 8

        # the config row writes its own audit row
        expected = (await board_client.get("/api/board/missions/m2/config")).json()["version"]
        await board_client.post("/api/board/missions/m2/config", json={
            "expected_version": expected, "hints": "audited",
        })
        assert await count() == baseline + 9

        # the live-state row writes its own audit row
        expected = (await board_client.get("/api/board/live-states/m2")).json()["live_state"]["updated_at"]
        await board_client.post("/api/board/live-states/m2", json={
            "expected_version": expected, "status": "active",
        })
        assert await count() == baseline + 10

        # queue add and queue remove each write the queue snapshot row
        await board_client.post("/api/board/queue/add", json={"mission_id": "m3"})
        assert await count() == baseline + 11
        await board_client.delete("/api/board/queue/m2", params={"confirm": "true"})
        assert await count() == baseline + 12

        # flag set and flag remove each write the flag row
        await board_client.post("/api/board/flags/pause", json={"value": "1"})
        assert await count() == baseline + 13
        await board_client.delete("/api/board/flags/pause")
        assert await count() == baseline + 14

        # template set and template remove each write the template row
        await board_client.post("/api/board/templates/base", json={"mission_id": "m2"})
        assert await count() == baseline + 15
        await board_client.delete("/api/board/templates/base")
        assert await count() == baseline + 16

        # the confirmed stop and the confirmed disable each write their live row
        await board_client.post("/api/board/missions/m2/stop", params={"confirm": "true"})
        assert await count() == baseline + 17
        await board_client.post("/api/board/missions/m2/disable", params={"confirm": "true"})
        assert await count() == baseline + 18


class TestErrorMapping:
    """The status mapping the board routes must keep: 409 on a version
    drift, 403 on a guarded action, 503 when the store is unavailable."""

    async def test_update_mission_with_stale_version_is_409(self, board_client):
        r = await board_client.get("/api/board/missions/m1")
        stale = r.json()["mission"]["updated_at"]
        await board_client.post("/api/board/missions/m1", json={
            "expected_version": stale, "objective": "first",
        })
        r = await board_client.post("/api/board/missions/m1", json={
            "expected_version": stale, "objective": "second",
        })
        assert r.status_code == 409
        assert r.json()["code"] == "conflict"

    async def test_guarded_actions_are_403(self, board_client):
        # stop a running mission without confirm
        await board_client.post("/api/board/live-states/sk1", json={
            "expected_version": (await board_client.get("/api/board/live-states/sk1"))
            .json()["live_state"]["updated_at"],
            "status": "active",
        })
        r = await board_client.post("/api/board/missions/m1/stop")
        assert r.status_code == 403
        assert r.json()["code"] == "guard"

    async def test_store_unavailable_is_503_on_the_stats_routes(self, settings):
        from claude_runner.api import create_app
        app = create_app(settings)
        await _run_lifespan(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for path in ("/api/board/missions/m1/stats",
                        "/api/board/missions/m1/readiness",
                        "/api/board/changelog"):
                r = await c.get(path)
                assert r.status_code == 503
                assert r.json()["code"] == "unavailable"


class TestSummary:
    """GET /api/board/summary: every mission in one response (state group,
    position, project, tasks, cached figures) and the projects (stored rules
    plus the default).  A failing or absent runner gives null figures and
    HTTP 200."""

    async def test_summary_shape_with_a_failing_manager(self, settings):
        """With a manager that raises, the figures are nulls but the route
        still answers HTTP 200 (a failing or absent runner never errors)."""
        from claude_runner.api import create_app
        from claude_runner.board import InMemoryBoardStore, Mission, QueueEntry

        class FailingManager:
            def __getattr__(self, name):
                raise RuntimeError("runner unreachable")

        store = InMemoryBoardStore()
        store._missions["m1"] = Mission(
            mission_id="m1", state_namespace="sk1", name="Source",
            working_directory="/tmp/x", rules=["r1"],
            acceptance_criteria=["c1"], retrieval_corpora=["corpus"],
            updated_at="2026-01-01T00:00:00+00:00",
        )
        store._queue["m1"] = QueueEntry(position=0, mission_id="m1", enabled=True)
        store._queue_order.append("m1")
        store.upsert_live_state("sk1", None, plan={"tasks": [{"id": "T1", "objective": "one"}]},
                              status="queued")
        app = create_app(settings)
        app.state.board_store = store
        # set the failing manager after the lifespan (the lifespan wires the real one)
        await _run_lifespan(app)
        app.state.manager = FailingManager()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/board/summary")
        assert r.status_code == 200
        body = r.json()
        assert "missions" in body and "projects" in body
        m = body["missions"][0]
        for field in ("mission_id", "name", "state_group", "position",
                      "project", "tasks", "attempts", "hours", "tokens",
                      "cache_ratio", "cost", "tok_s", "thinking_share"):
            assert field in m
        # the manager raises: every figure is null
        for field in ("attempts", "hours", "tokens", "cache_ratio", "cost",
                      "tok_s", "thinking_share"):
            assert m[field] is None
        # the single mission is in the default project (its last folder name)
        assert len(body["projects"]) == 1
        assert body["projects"][0]["mission_ids"] == ["m1"]

    async def test_summary_project_rule_merges_matching_folders(self, settings):
        """A stored project rule 'D:/ClaudeProjects/model-bench/*/rag-platform'
        merges missions whose working directory matches it into one project."""
        from claude_runner.api import create_app
        from claude_runner.board import InMemoryBoardStore, Mission, QueueEntry

        store = InMemoryBoardStore()
        store.put_project_rules(
            [{"name": "model-bench",
              "pattern": "D:/ClaudeProjects/model-bench/*/rag-platform",
              "position": 1}],
            None,
        )
        store._missions["mb1"] = Mission(
            mission_id="mb1", state_namespace="skb1", name="Bench A",
            working_directory="D:/ClaudeProjects/model-bench/run1/rag-platform",
            rules=["r1"], acceptance_criteria=["c1"], retrieval_corpora=["c"],
            updated_at="2026-01-01T00:00:00+00:00",
        )
        store._missions["mb2"] = Mission(
            mission_id="mb2", state_namespace="skb2", name="Bench B",
            working_directory="D:/ClaudeProjects/model-bench/run2/rag-platform",
            rules=["r1"], acceptance_criteria=["c1"], retrieval_corpora=["c"],
            updated_at="2026-01-01T00:00:00+00:00",
        )
        store._queue["mb1"] = QueueEntry(position=0, mission_id="mb1", enabled=True)
        store._queue["mb2"] = QueueEntry(position=1, mission_id="mb2", enabled=True)
        store._queue_order.append("mb1")
        store._queue_order.append("mb2")
        store.upsert_live_state("skb1", None, plan={"tasks": []}, status="queued")
        store.upsert_live_state("skb2", None, plan={"tasks": []}, status="queued")
        app = create_app(settings)
        app.state.board_store = store
        await _run_lifespan(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/board/summary")
        assert r.status_code == 200
        body = r.json()
        # both missions match the stored rule: one project, two mission_ids
        assert len(body["projects"]) == 1
        p = body["projects"][0]
        assert p["key"] == "model-bench"
        assert p["name"] == "model-bench"
        assert p["rule"] == "D:/ClaudeProjects/model-bench/*/rag-platform"
        assert sorted(p["mission_ids"]) == ["mb1", "mb2"]
        # each mission carries the project
        for m in body["missions"]:
            assert m["project"] == {"key": "model-bench", "name": "model-bench",
                                    "rule": "D:/ClaudeProjects/model-bench/*/rag-platform"}
        # positions are taken from the queue
        by_id = {m["mission_id"]: m for m in body["missions"]}
        assert by_id["mb1"]["position"] == 0
        assert by_id["mb2"]["position"] == 1

    async def test_summary_task_status_mapping_and_attempts(self, settings):
        """The summary maps the mission-card status to its vocabulary and flags
        an accepted task retried to ``accepted_retry``; task ids are parsed as
        T[0-9]+[A-Z]*."""
        from claude_runner.api import create_app
        from claude_runner.board import InMemoryBoardStore, Mission, QueueEntry

        store = InMemoryBoardStore()
        store._missions["m1"] = Mission(
            mission_id="m1", state_namespace="sk1", name="Source",
            working_directory="/tmp/x", rules=["r1"],
            acceptance_criteria=["c1"], retrieval_corpora=["c"],
            updated_at="2026-01-01T00:00:00+00:00",
        )
        store._queue["m1"] = QueueEntry(position=0, mission_id="m1", enabled=True)
        store._queue_order.append("m1")
        store.upsert_live_state(
            "sk1", None,
            plan={"tasks": [
                {"id": "T1", "objective": "one", "dependencies": ["T0"]},
                {"id": "T2", "objective": "two"},
            ]},
            status="active", task_index=1,
        )
        # T1 is behind the current task (task_index=1 -> T2 is current): its
        # last review_status is "pass", so it derives to "accepted"; it has
        # two attempts, hence "accepted_retry" in the summary.
        store.append_event("sk1", {
            "task_id": "T1", "task_attempt": 1,
            "event_type": "review_parsed",
            "review_reason": "all good", "review_status": "pass", "review_score": 5,
            "worker_run_id": "run_a",
        })
        store.append_event("sk1", {
            "task_id": "T1", "task_attempt": 2,
            "event_type": "review_parsed",
            "review_reason": "all good", "review_status": "pass", "review_score": 5,
            "worker_run_id": "run_b",
        })
        # T2 is the current task (task_index=1) with no events yet: "running"
        app = create_app(settings)
        app.state.board_store = store
        await _run_lifespan(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/board/summary")
        assert r.status_code == 200
        tasks = r.json()["missions"][0]["tasks"]
        by_id = {t["id"]: t for t in tasks}
        # T1 is accepted and retried twice -> accepted_retry, attempts 2
        assert by_id["T1"]["status"] == "accepted_retry"
        assert by_id["T1"]["attempts"] == 2
        assert by_id["T1"]["deps"] == ["T0"]
        # T2 is the current task, no events: running
        assert by_id["T2"]["status"] == "running"
        assert by_id["T2"]["attempts"] == 0


class TestTimeline:
    """GET /api/board/missions/{id}/timeline: accepted reviews and the
    obstacles (rejected_3x, timeout, interrupted) from the event log and the
    manager; empty lists on missing data or a missing/failing manager."""

    def _store_with_events(self, settings, events):
        from claude_runner.api import create_app

        store = InMemoryBoardStore()
        store._missions["m1"] = Mission(
            mission_id="m1", state_namespace="sk1", name="Source",
            working_directory="/tmp/x", rules=["r1"],
            acceptance_criteria=["c1"], retrieval_corpora=["corpus"],
            updated_at="2026-01-01T00:00:00+00:00",
        )
        store._queue["m1"] = QueueEntry(position=0, mission_id="m1", enabled=True)
        store._queue_order.append("m1")
        store.upsert_live_state("sk1", None, plan={"tasks": []}, status="queued")
        for e in events:
            store.append_event("sk1", e)
        app = create_app(settings)
        app.state.board_store = store
        return app

    async def test_timeline_no_events_returns_empty_lists(self, settings):
        """A mission with no events: 200 with both lists empty."""
        app = self._store_with_events(settings, [])
        await _run_lifespan(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/board/missions/m1/timeline")
        assert r.status_code == 200
        assert r.json() == {"accepted": [], "obstacles": []}

    async def test_timeline_accepted_and_rejected_3x(self, settings):
        """A pass review (carrying only review_reason, as the events store it)
        lands in accepted; three needs_changes reviews give a rejected_3x
        obstacle.  ts may be None: assert only on task and kind."""
        app = self._store_with_events(settings, [
            {"task_id": "T1", "event_type": "review_parsed",
             "review_reason": "pass"},
            {"task_id": "T2", "event_type": "review_parsed",
             "review_reason": "needs_changes"},
            {"task_id": "T2", "event_type": "review_parsed",
             "review_reason": "needs_changes"},
            {"task_id": "T2", "event_type": "review_parsed",
             "review_reason": "needs_changes"},
        ])
        await _run_lifespan(app)
        app.state.manager = None
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/board/missions/m1/timeline")
        assert r.status_code == 200
        body = r.json()
        assert len(body["accepted"]) == 1
        assert body["accepted"][0]["task"] == "T1"
        assert len(body["obstacles"]) == 1
        assert body["obstacles"][0]["kind"] == "rejected_3x"
        assert body["obstacles"][0]["task"] == "T2"

    async def test_timeline_missing_or_failing_manager_gives_empty_lists(self, settings):
        """manager=None and a manager that raises both answer 200 with both
        lists empty, even when events are present."""
        app = self._store_with_events(settings, [
            {"task_id": "T1", "event_type": "review_parsed",
             "review_reason": "pass", "worker_run_id": "run_a"},
        ])
        await _run_lifespan(app)
        app.state.manager = None
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/board/missions/m1/timeline")
        assert r.status_code == 200
        assert r.json() == {"accepted": [], "obstacles": []}

        class FailingManager:
            def __getattr__(self, name):
                raise RuntimeError("runner unreachable")

        app.state.manager = FailingManager()
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/board/missions/m1/timeline")
        assert r.status_code == 200
        assert r.json() == {"accepted": [], "obstacles": []}

    async def test_timeline_unknown_mission_gives_404(self, settings):
        app = self._store_with_events(settings, [])
        await _run_lifespan(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/board/missions/unknown/timeline")
        assert r.status_code == 404


PLAN_TASKS = [
    {"id": "T001", "objective": "one"},
    {"id": "T002", "objective": "two"},
    {"id": "T003", "objective": "three"},
    {"id": "T004", "objective": "four"},
]
STALE_VERSION = "1999-01-01T00:00:00+00:00"


@asynccontextmanager
async def _plan_client(settings, tasks=PLAN_TASKS, status="queued", task_index=0, events=()):
    """A client on mission m1 whose live row holds ``tasks``; the live task is ``task_index``."""
    from claude_runner.api import create_app

    store = InMemoryBoardStore()
    store._missions["m1"] = Mission(
        mission_id="m1", state_namespace="sk1", name="Source",
        working_directory="/tmp/x", rules=["r1"],
        acceptance_criteria=["c1"], retrieval_corpora=["corpus"],
        updated_at="2026-01-01T00:00:00+00:00",
    )
    store._queue["m1"] = QueueEntry(position=0, mission_id="m1", enabled=True)
    store._queue_order.append("m1")
    store.upsert_live_state("sk1", None, plan={"tasks": copy.deepcopy(tasks)},
                            status=status, task_index=task_index, task_attempt=0)
    for e in events:
        store.append_event("sk1", e)
    app = create_app(settings)
    app.state.board_store = store
    await _run_lifespan(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, store


async def _live(c) -> dict:
    return (await c.get("/api/board/live-states/sk1")).json()["live_state"]


def _task(plan: dict, objective: str) -> dict:
    return next(t for t in plan["tasks"] if t["objective"] == objective)


def _objectives(plan: dict) -> list[str]:
    return [t["objective"] for t in plan["tasks"]]


def _ui_check(needed: bool = False, **over) -> dict:
    check = {"needed": needed, "why": "shows the form", "scenario": "submit", "steps": ["open"]}
    check.update(over)
    return check


class TestPlanUiCheckAndQa:
    """The optional per-task ui_check and plan-level qa: old plans stay valid
    and unchanged, the fields survive a save, and malformed values get a 400
    with the reason."""

    async def test_plan_without_the_fields_is_saved_unchanged(self, settings):
        plan = {"tasks": [{"id": "T1", "objective": "one"}]}
        async with _plan_client(settings) as (c, _store):
            live = await _live(c)
            r = await c.post("/api/board/live-states/sk1", json={
                "expected_version": live["updated_at"], "plan": copy.deepcopy(plan),
            })
            assert r.status_code == 200
            assert r.json()["live_state"]["plan"] == plan

    async def test_ui_check_and_qa_round_trip_through_a_save(self, settings):
        plan = {
            "tasks": [
                {"id": "T1", "objective": "one", "ui_check": _ui_check(needed=True)},
                {"id": "T2", "objective": "two", "ui_check": _ui_check(needed=False)},
            ],
            "qa": {"needed": True, "reasons": ["form"], "scenarios": ["submit"]},
        }
        async with _plan_client(settings) as (c, _store):
            live = await _live(c)
            r = await c.post("/api/board/live-states/sk1", json={
                "expected_version": live["updated_at"], "plan": copy.deepcopy(plan),
            })
            assert r.status_code == 200
            assert r.json()["live_state"]["plan"] == plan
            assert (await _live(c))["plan"] == plan

    @pytest.mark.parametrize("check, reason", [
        ({"needed": "yes", "why": "w", "scenario": "s", "steps": []}, "ui_check.needed"),
        ({"needed": True, "why": "w", "scenario": "s"}, "ui_check must be an object"),
        ({"needed": True, "why": "w", "scenario": "s", "steps": "open"}, "ui_check.steps"),
        ({"needed": True, "why": 3, "scenario": "s", "steps": []}, "ui_check.why"),
    ])
    async def test_malformed_ui_check_is_400_with_the_reason(self, settings, check, reason):
        plan = {"tasks": [{"id": "T1", "objective": "one", "ui_check": check}]}
        async with _plan_client(settings) as (c, _store):
            live = await _live(c)
            r = await c.post("/api/board/live-states/sk1", json={
                "expected_version": live["updated_at"], "plan": plan,
            })
            assert r.status_code == 400
            assert r.json()["code"] == "bad_request"
            assert reason in r.json()["error"]["message"]

    async def test_qa_needed_without_a_needed_ui_check_is_400(self, settings):
        plan = {
            "tasks": [{"id": "T1", "objective": "one", "ui_check": _ui_check(needed=False)}],
            "qa": {"needed": True, "reasons": ["form"], "scenarios": ["submit"]},
        }
        async with _plan_client(settings) as (c, _store):
            live = await _live(c)
            r = await c.post("/api/board/live-states/sk1", json={
                "expected_version": live["updated_at"], "plan": plan,
            })
            assert r.status_code == 400
            assert "no task has a needed ui_check" in r.json()["error"]["message"]

    async def test_more_than_three_needed_ui_checks_is_400(self, settings):
        plan = {"tasks": [
            {"id": f"T{i}", "objective": f"o{i}", "ui_check": _ui_check(needed=True)}
            for i in range(1, 5)
        ]}
        async with _plan_client(settings) as (c, _store):
            live = await _live(c)
            r = await c.post("/api/board/live-states/sk1", json={
                "expected_version": live["updated_at"], "plan": plan,
            })
            assert r.status_code == 400
            assert "at most 3" in r.json()["error"]["message"]

    async def test_create_rejects_a_malformed_ui_check(self, board_client):
        r = await board_client.post("/api/board/missions/create", json={
            "mission_id": "m2", "source_mission_id": "m1", "objective": "x",
            "plan": {"tasks": [{"id": "T1", "ui_check": {"needed": 1}}]},
        })
        assert r.status_code == 400
        assert "ui_check" in r.json()["error"]["message"]

    async def test_create_accepts_a_valid_ui_check(self, board_client):
        r = await board_client.post("/api/board/missions/create", json={
            "mission_id": "m2", "source_mission_id": "m1", "objective": "x",
            "plan": {"tasks": [{"id": "T1", "ui_check": _ui_check(needed=True)}]},
        })
        assert r.status_code == 200


class TestPlanWrites:
    """Reorder, dependency and move writes on the in-memory store: the status
    codes (200, 403 guard, 409 conflict, 422 cycle) and running_warning."""

    async def test_reorder_independent_waiting_tasks_logs_a_change(self, settings):
        async with _plan_client(settings) as (c, store):
            before = len(store.changelog(1000))
            live = await _live(c)
            r = await c.post("/api/board/missions/m1/tasks/reorder", json={
                "expected_version": live["updated_at"], "order": ["T004", "T003", "T002"],
            })
            assert r.status_code == 200
            body = r.json()
            assert _objectives(body["plan"]) == ["one", "four", "three", "two"]
            assert body["changelog"]["action"] == "task reorder"
            assert body["running_warning"] is False
            assert len(store.changelog(1000)) > before
            assert any(e.action == "live state upsert" for e in store.changelog(1000))

    async def test_reorder_breaking_a_dependency_gives_403(self, settings):
        tasks = copy.deepcopy(PLAN_TASKS)
        tasks[1]["dependencies"] = ["T004"]
        async with _plan_client(settings, tasks=tasks) as (c, _):
            r = await c.post("/api/board/missions/m1/tasks/reorder", json={
                "expected_version": (await _live(c))["updated_at"],
                "order": ["T002", "T003", "T004"],
            })
            assert r.status_code == 403
            assert r.json()["code"] == "guard"
            assert r.json()["broken"] == [["T004", "T002"]]
            assert _objectives((await _live(c))["plan"]) == ["one", "two", "three", "four"]

    async def test_reorder_moving_the_running_task_gives_403(self, settings):
        async with _plan_client(settings) as (c, _):
            r = await c.post("/api/board/missions/m1/tasks/reorder", json={
                "expected_version": (await _live(c))["updated_at"],
                "order": ["T001", "T002", "T003", "T004"],
            })
            assert r.status_code == 403
            assert r.json()["code"] == "guard"
            assert r.json()["locked"] == ["T001"]

    async def test_reorder_stale_version_gives_409(self, settings):
        async with _plan_client(settings) as (c, _):
            r = await c.post("/api/board/missions/m1/tasks/reorder", json={
                "expected_version": STALE_VERSION, "order": ["T004", "T003", "T002"],
            })
            assert r.status_code == 409
            assert r.json()["code"] == "conflict"
            assert _objectives((await _live(c))["plan"]) == ["one", "two", "three", "four"]

    async def test_add_then_remove_dependency(self, settings):
        async with _plan_client(settings) as (c, _):
            r = await c.post("/api/board/missions/m1/tasks/T004/dependencies", json={
                "expected_version": (await _live(c))["updated_at"],
                "action": "add", "dependency": "T002",
            })
            assert r.status_code == 200
            body = r.json()
            assert body["changelog"]["action"] == "task dependency add"
            assert _task(body["plan"], "four")["dependencies"] == ["T002"]
            assert body["running_warning"] is False

            r = await c.post("/api/board/missions/m1/tasks/T004/dependencies", json={
                "expected_version": (await _live(c))["updated_at"],
                "action": "remove", "dependency": "T002",
            })
            assert r.status_code == 200
            assert r.json()["changelog"]["action"] == "task dependency remove"
            assert _task(r.json()["plan"], "four")["dependencies"] == []

    async def test_dependency_closing_a_cycle_gives_422_with_the_pair(self, settings):
        tasks = copy.deepcopy(PLAN_TASKS)
        tasks[2]["dependencies"] = ["T002"]
        async with _plan_client(settings, tasks=tasks) as (c, _):
            r = await c.post("/api/board/missions/m1/tasks/T002/dependencies", json={
                "expected_version": (await _live(c))["updated_at"],
                "action": "add", "dependency": "T003",
            })
            assert r.status_code == 422
            assert r.json()["code"] == "cycle"
            assert r.json()["closing_pair"] == ["T003", "T002"]
            assert _task((await _live(c))["plan"], "two").get("dependencies", []) == []

    async def test_new_dependency_already_violated_repairs_the_order(self, settings):
        async with _plan_client(settings) as (c, _):
            r = await c.post("/api/board/missions/m1/tasks/T002/dependencies", json={
                "expected_version": (await _live(c))["updated_at"],
                "action": "add", "dependency": "T004",
            })
            assert r.status_code == 200
            plan = r.json()["plan"]
            assert _objectives(plan) == ["one", "three", "four", "two"]
            assert _task(plan, "two")["dependencies"] == [
                next(t["id"] for t in plan["tasks"] if t["objective"] == "four"),
            ]

    async def test_dependency_stale_version_gives_409(self, settings):
        async with _plan_client(settings) as (c, _):
            r = await c.post("/api/board/missions/m1/tasks/T002/dependencies", json={
                "expected_version": STALE_VERSION, "action": "add", "dependency": "T004",
            })
            assert r.status_code == 409
            assert r.json()["code"] == "conflict"

    async def test_dependency_on_the_running_task_gives_403(self, settings):
        async with _plan_client(settings) as (c, _):
            r = await c.post("/api/board/missions/m1/tasks/T001/dependencies", json={
                "expected_version": (await _live(c))["updated_at"],
                "action": "add", "dependency": "T002",
            })
            assert r.status_code == 403
            assert r.json()["code"] == "guard"
            assert r.json()["locked"] == ["T001"]

    async def test_running_warning_for_an_active_mission(self, settings):
        async with _plan_client(settings, status="active") as (c, _):
            r = await c.post("/api/board/missions/m1/tasks/reorder", json={
                "expected_version": (await _live(c))["updated_at"],
                "order": ["T004", "T003", "T002"],
            })
            assert r.status_code == 200
            assert r.json()["running_warning"] is True

            r = await c.post("/api/board/missions/m1/tasks/T002/dependencies", json={
                "expected_version": (await _live(c))["updated_at"],
                "action": "add", "dependency": "T003",
            })
            assert r.status_code == 200
            assert r.json()["running_warning"] is True

    async def test_move_running_task_gives_403(self, settings):
        async with _plan_client(settings) as (c, _):
            r = await c.post("/api/board/missions/m1/tasks/T001/move", json={
                "expected_version": (await _live(c))["updated_at"], "to_position": 2,
            })
            assert r.status_code == 403
            assert r.json()["code"] == "guard"

    async def test_move_accepted_task_gives_403(self, settings):
        events = [{"task_id": "T001", "event_type": "review_parsed", "review_status": "pass"}]
        async with _plan_client(settings, task_index=1, events=events) as (c, _):
            r = await c.post("/api/board/missions/m1/tasks/T001/move", json={
                "expected_version": (await _live(c))["updated_at"], "to_position": 3,
            })
            assert r.status_code == 403
            assert r.json()["code"] == "guard"

    async def test_move_stale_version_gives_409(self, settings):
        async with _plan_client(settings) as (c, _):
            r = await c.post("/api/board/missions/m1/tasks/T003/move", json={
                "expected_version": STALE_VERSION, "to_position": 1,
            })
            assert r.status_code == 409
            assert r.json()["code"] == "conflict"


def test_queue_version_accepts_a_method() -> None:
    """The Pg store computes the queue version in a method; a bound method must never reach the JSON response."""
    from claude_runner.board_api import _queue_version

    class WithMethod:
        def _queue_version(self) -> str:
            return "2026-10-17T00:00:00+00:00"

    class WithStamp:
        _queue_version = "v7"

    class Empty:
        _queue_version = ""

    assert _queue_version(WithMethod()) == "2026-10-17T00:00:00+00:00"
    assert _queue_version(WithStamp()) == "v7"
    assert _queue_version(Empty()) is None


QA_SCENARIO = "kanban stale queue version shows the refresh message"
PNG_BYTES = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def qa_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    runs = tmp_path / "runs"
    nightly = tmp_path / "nightly-verdict.json"
    monkeypatch.setattr(board_api, "RUNS_DIR", runs)
    monkeypatch.setattr(board_api, "NIGHTLY_VERDICT_PATH", nightly)
    return runs, nightly


async def _plan_qa(client, scenarios: list[str]) -> None:
    store = client._transport.app.state.board_store
    plan = {"tasks": [{"id": "T1", "objective": "one"}],
            "qa": {"needed": True, "reasons": ["ui"], "scenarios": scenarios}}
    store.upsert_live_state("sk1", None, plan=plan, status="queued")


def _write_run(runs: Path, name: str, verdict: dict) -> Path:
    run = runs / name
    run.mkdir(parents=True)
    (run / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
    (run / "step-2.png").write_bytes(PNG_BYTES)
    return run


class TestMissionQa:
    async def test_no_verdict_is_an_empty_answer(self, board_client, qa_dirs) -> None:
        await _plan_qa(board_client, [QA_SCENARIO])
        response = await board_client.get("/api/board/missions/m1/qa")
        assert response.status_code == 200
        assert response.json() == {"verdict": None}

    async def test_plan_without_scenarios_gets_no_verdict(self, board_client, qa_dirs) -> None:
        runs, _ = qa_dirs
        _write_run(runs, "qa-ab12", build_verdict(scenario=QA_SCENARIO, failed_step=2))
        await _plan_qa(board_client, [])
        response = await board_client.get("/api/board/missions/m1/qa")
        assert response.json() == {"verdict": None}

    async def test_failing_verdict_names_the_step_and_links_the_screenshot(
        self, board_client, qa_dirs
    ) -> None:
        runs, _ = qa_dirs
        _write_run(runs, "qa-ab12", build_verdict(
            scenario=QA_SCENARIO, failed_step=2, failed_reason="no refresh message",
            screenshots=["qa-ab12/step-2.png"]))
        await _plan_qa(board_client, [QA_SCENARIO])
        verdict = (await board_client.get("/api/board/missions/m1/qa")).json()["verdict"]
        assert verdict["verdict"] == "fail"
        assert verdict["failed_step"] == 2
        assert verdict["failed_reason"] == "no refresh message"
        assert verdict["screenshots"] == [
            {"name": "qa-ab12/step-2.png", "url": "/api/board/qa/screenshots/qa-ab12/step-2.png"}
        ]

    async def test_blocked_verdict_carries_its_reason(self, board_client, qa_dirs) -> None:
        runs, _ = qa_dirs
        _write_run(runs, "qa-cd34", build_verdict(
            scenario=QA_SCENARIO, blocked_reason="stack did not start"))
        await _plan_qa(board_client, [QA_SCENARIO])
        verdict = (await board_client.get("/api/board/missions/m1/qa")).json()["verdict"]
        assert verdict["verdict"] == "blocked"
        assert verdict["blocked_reason"] == "stack did not start"

    async def test_nightly_file_is_read_when_no_run_matches(self, board_client, qa_dirs) -> None:
        _, nightly = qa_dirs
        nightly.write_text(json.dumps([build_verdict(scenario=QA_SCENARIO)]), encoding="utf-8")
        await _plan_qa(board_client, [QA_SCENARIO])
        verdict = (await board_client.get("/api/board/missions/m1/qa")).json()["verdict"]
        assert verdict["verdict"] == "pass"

    async def test_text_is_redacted_on_the_way_out(self, board_client, qa_dirs) -> None:
        runs, _ = qa_dirs
        secret = "sk-abcdefghijklmnopqrstuvwxyz0123"
        _write_run(runs, "qa-ab12", build_verdict(
            scenario=QA_SCENARIO, failed_step=0, failed_reason=f"token {secret}"))
        await _plan_qa(board_client, [QA_SCENARIO])
        response = await board_client.get("/api/board/missions/m1/qa")
        assert secret not in response.text

    async def test_unknown_mission_is_not_found(self, board_client, qa_dirs) -> None:
        response = await board_client.get("/api/board/missions/nope/qa")
        assert response.status_code == 404

    async def test_screenshot_is_served_from_the_runs_directory(
        self, board_client, qa_dirs
    ) -> None:
        runs, _ = qa_dirs
        _write_run(runs, "qa-ab12", build_verdict(scenario=QA_SCENARIO))
        response = await board_client.get("/api/board/qa/screenshots/qa-ab12/step-2.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == PNG_BYTES

    async def test_traversal_out_of_the_runs_directory_is_refused(
        self, board_client, qa_dirs, tmp_path
    ) -> None:
        runs, _ = qa_dirs
        _write_run(runs, "qa-ab12", build_verdict(scenario=QA_SCENARIO))
        (tmp_path / "secret.png").write_bytes(PNG_BYTES)
        for path in ("qa-ab12/..%2F..%2Fsecret.png", "..%2F..%2Fsecret.png/x.png",
                     "qa-ab12/%2e%2e%2fsecret.png", "qa-ab12/notes.txt"):
            response = await board_client.get(f"/api/board/qa/screenshots/{path}")
            assert response.status_code == 404, path
