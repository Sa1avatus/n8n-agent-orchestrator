"""The mission-board HTTP routes (claude_runner/board_api.py): optimistic
locking (409 on a version drift), the confirm gate on dangerous actions,
every write landing in board.changelog, and the 503 with no DSN when the
backend is unavailable.  T015 adds the 409/403/503 mapping on the stats and
readiness routes, the start blocked by an error without confirmation, the
rollback success and conflict, and the dangerous values (script tags and
quotes) answered as data with the HTML/JS never interpolating them
unescaped (board.js uses ``esc()`` for every ``innerHTML`` value)."""

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
            # the reason says the DSN is unset but never carries the DSN itself
            assert "не задан" in r.json()["reason"]
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
