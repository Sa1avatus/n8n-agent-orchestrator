"""Readiness checks, start guard, rollback and mission creation
(claude_runner/board_ready.py): each check alone, the start guard,
the optimistic rollback against a drift, and the atomic mission creation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from claude_runner.board import ConflictError, InMemoryBoardStore, LiveState, Mission, QueueEntry
from claude_runner.board_ready import (
    MissionIdError,
    check_live_state_present,
    check_llama_model,
    check_not_running,
    check_plan_present,
    check_preflight_fresh,
    check_required_files,
    check_runner_health,
    check_working_folder,
    collect_readiness,
    create_mission,
    rollback,
    save_template,
    start_guard,
)


@pytest.fixture
def board() -> InMemoryBoardStore:
    """A fresh in-memory store (no seed: put rows in per test)."""
    return InMemoryBoardStore()


def seed(board: InMemoryBoardStore) -> None:
    """The store updates rows but has no create: put the mission row in directly."""
    board._missions["m1"] = Mission(
        mission_id="m1", state_namespace="sk1", name="Source",
        working_directory="/tmp/x", rules=["r1"],
        acceptance_criteria=["c1"], retrieval_corpora=["corpus"],
    )
    board.set_mission_config(
        "m1", {"required_files": ["/tmp/x/a.md"], "hints": "HINTS"}, None
    )
    board._queue["m1"] = QueueEntry(position=0, mission_id="m1", enabled=True)
    board._queue_order.append("m1")


# -------------------------------------------------------------------------- checks


class TestWorkingFolder:
    def test_missing_folder_is_an_error(self):
        item = check_working_folder("/no/such/dir")
        assert item == {"check": "working_folder", "status": "error",
                       "reason": "working folder '/no/such/dir' does not exist"}

    def test_present_folder_is_ok(self):
        item = check_working_folder("/tmp")
        assert item["status"] == "ok"
        assert item["reason"] == "/tmp"

    def test_unset_folder_is_a_warning(self):
        assert check_working_folder(None) == {
            "check": "working_folder", "status": "warning",
            "reason": "working folder is not set",
        }

    def test_host_path_is_a_warning(self):
        item = check_working_folder("/host/only/path", host_visible=False)
        assert item["status"] == "warning"
        assert item["reason"] == "проверяется на хосте"


class TestRequiredFiles:
    def test_no_files_is_ok(self):
        assert check_required_files(None) == {
            "check": "required_files", "status": "ok",
            "reason": "no required files declared",
        }

    def test_missing_files_are_an_error(self):
        item = check_required_files(["/no/such/a.md", "/tmp/x/a.md"])
        assert item["status"] == "error"
        assert "/no/such/a.md" in item["reason"]
        assert item["files"] == ["/no/such/a.md", "/tmp/x/a.md"]

    def test_host_files_are_a_warning(self):
        item = check_required_files(["/host/file.md"], host_visible=False)
        assert item["status"] == "warning"
        assert item["reason"] == "проверяется на хосте"


class TestRunnerHealth:
    def test_missing_url_is_a_warning(self):
        item = asyncio.run(check_runner_health(""))
        assert item == {"check": "runner", "status": "warning",
                       "reason": "runner URL is not set"}

    def test_unreachable_url_is_an_error(self):
        item = asyncio.run(check_runner_health("http://127.0.0.1:59999"))
        assert item["status"] == "error"
        assert "runner unreachable" in item["reason"]

    def test_health_not_ok_is_an_error(self):
        import http.server
        import json as _json
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = _json.dumps({"status": "degraded"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            item = asyncio.run(check_runner_health(url))
        finally:
            server.shutdown()
        assert item["status"] == "error"
        assert "not 'ok'" in item["reason"]

    def test_healthy_runner_is_ok(self):
        import http.server
        import json as _json
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = _json.dumps({"status": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            item = asyncio.run(check_runner_health(url))
        finally:
            server.shutdown()
        assert item["status"] == "ok"
        assert "is up" in item["reason"]


class TestLitellmHealth:
    def test_missing_url_is_a_warning(self):
        from claude_runner.board_ready import check_litellm_health

        item = asyncio.run(check_litellm_health(""))
        assert item == {"check": "litellm", "status": "warning",
                       "reason": "litellm URL is not set"}

    def test_unreachable_url_is_an_error(self):
        from claude_runner.board_ready import check_litellm_health

        item = asyncio.run(check_litellm_health("http://127.0.0.1:59999"))
        assert item["status"] == "error"
        assert "litellm unreachable" in item["reason"]

    def test_liveliness_not_ok_is_an_error(self):
        import http.server
        import threading

        from claude_runner.board_ready import check_litellm_health

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(503)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            item = asyncio.run(check_litellm_health(url))
        finally:
            server.shutdown()
        assert item["status"] == "error"
        assert "HTTP 503" in item["reason"]


class TestLlamaModel:
    def test_missing_url_is_a_warning(self):
        item = check_llama_model("", "k", "model")
        assert item == {"check": "llama_model", "status": "warning",
                       "reason": "llama probe URL is not set"}

    def test_missing_model_is_a_warning(self):
        item = check_llama_model("http://x", "k", "")
        assert item == {"check": "llama_model", "status": "warning",
                       "reason": "worker_model is not set"}

    def test_unreachable_server_is_an_error(self):
        item = check_llama_model("http://127.0.0.1:59999", "k", "model")
        assert item["status"] == "error"
        assert "llama-server unreachable" in item["reason"]

    def test_model_not_listed_is_an_error(self):
        import http.server
        import json as _json
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = _json.dumps({"data": [{"id": "other-model"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            item = check_llama_model(url, "", "model")
        finally:
            server.shutdown()
        assert item["status"] == "error"
        assert "not in llama-server's /v1/models" in item["reason"]

    def test_listed_model_with_ctx_size_is_ok(self):
        import http.server
        import json as _json
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = _json.dumps({"data": [{
                    "id": "model",
                    "status": {"args": ["--ctx-size", "32768", "--parallel", "2"]},
                }]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            item = check_llama_model(url, "", "model")
        finally:
            server.shutdown()
        assert item["status"] == "ok"
        assert item["reason"] == "model 'model' is up (context 16384 tokens)"


class TestPlanAndLiveState:
    def test_plan_check(self):
        assert check_plan_present(None) == {
            "check": "plan", "status": "error",
            "reason": "the mission has no plan in the Live State row",
        }
        # a plan with no tasks is an error too
        item = check_plan_present(LiveState(state_key="sk", plan={"tasks": []}))
        assert item["status"] == "error"
        assert "no tasks" in item["reason"]
        live = LiveState(state_key="sk", plan={"tasks": [{"id": "T1"}]})
        item = check_plan_present(live)
        assert item["status"] == "ok"
        assert item["reason"] == "plan with 1 task(s)"

    def test_live_state_check(self):
        assert check_live_state_present(None) == {
            "check": "live_state", "status": "error",
            "reason": "the mission has no Live State row",
        }
        live = LiveState(state_key="sk", status="active")
        assert check_live_state_present(live)["status"] == "ok"


class TestNotRunning:
    def test_running_mission_is_an_error(self, board: InMemoryBoardStore):
        board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1")
        board.upsert_live_state("sk1", None, status="active")
        item = check_not_running(board, "m1")
        assert item["status"] == "error"
        assert "is running" in item["reason"]

    def test_idle_mission_is_ok(self, board: InMemoryBoardStore):
        board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1")
        board.upsert_live_state("sk1", None, status="queued")
        assert check_not_running(board, "m1")["status"] == "ok"


class TestPreflight:
    def test_no_preflight_is_an_error(self, board: InMemoryBoardStore):
        item = check_preflight_fresh(board, "m1")
        assert item["status"] == "error"
        assert "no preflight recorded" in item["reason"]

    def test_fresh_preflight_is_ok(self, board: InMemoryBoardStore):
        board.record_preflight("m1", [{"check": "x"}])
        item = check_preflight_fresh(board, "m1")
        assert item["status"] == "ok"
        assert item["checked_at"]


    def test_stale_preflight_is_a_warning(self, board: InMemoryBoardStore):
        board.record_preflight("m1", [{"check": "x"}])
        item = check_preflight_fresh(board, "m1", max_age_seconds=-1)
        assert item["status"] == "warning"
        assert "s old" in item["reason"]


# -------------------------------------------------------------------------- guard


class TestStartGuard:
    def test_errors_block_the_start(self):
        summary = {"errors": [{"check": "runner", "status": "error",
                              "reason": "down"}]}
        guard = start_guard(summary)
        assert guard["allowed"] is False
        assert guard["blocked_by"] == summary["errors"]
        assert "runner" in guard["reason"]

    def test_confirm_overrides_the_block(self):
        summary = {"errors": [{"check": "runner", "status": "error", "reason": "down"}]}
        guard = start_guard(summary, confirmed=True)
        assert guard["allowed"] is True
        assert guard["blocked_by"] == []
        assert guard["confirmed"] is True

    def test_no_errors_allow_the_start(self):
        summary = {"errors": []}
        guard = start_guard(summary)
        assert guard["allowed"] is True


# -------------------------------------------------------------------------- rollback


class TestRollback:
    def test_backup_row_is_saved_before_the_change(self, board: InMemoryBoardStore):
        seed(board)
        board._missions["m1"].objective = ""
        before = board.get_mission("m1").updated_at
        board.update_mission("m1", before, objective="new")
        entry = next(e for e in board.changelog()
                    if e.action == "mission update" and e.mission_id == "m1")
        # the changelog row keeps the full row exactly as it was before the
        # change (the backup row)
        assert entry.before_json["objective"] == ""
        assert entry.before_json["name"] == "Source"
        assert entry.before_json["working_directory"] == "/tmp/x"
        assert entry.before_json["updated_at"] == before
        # and it differs from the row after the change
        assert entry.after_json["objective"] == "new"

    def test_rollback_restores_the_previous_value(self, board: InMemoryBoardStore):
        seed(board)
        board._missions["m1"].objective = ""
        before = board.get_mission("m1").updated_at
        board.update_mission("m1", before, objective="new")
        entry_id = next(e.id for e in board.changelog()
                        if e.action == "mission update" and e.mission_id == "m1")
        result = rollback(board, entry_id)
        assert result["restored"]["objective"] == ""
        assert board.get_mission("m1").objective == ""

    def test_rollback_raises_when_the_row_changed_after_the_entry(
        self, board: InMemoryBoardStore
    ):
        seed(board)
        before = board.get_mission("m1").updated_at
        board.update_mission("m1", before, objective="new")
        entry_id = next(e.id for e in board.changelog()
                        if e.action == "mission update" and e.mission_id == "m1")
        # a second change after the entry: the entry's after_json no longer
        # matches the row, so the rollback is refused
        before2 = board.get_mission("m1").updated_at
        board.update_mission("m1", before2, objective="newer")
        with pytest.raises(ConflictError):
            rollback(board, entry_id)

    def test_rollback_of_a_live_state_row(self, board: InMemoryBoardStore):
        board.upsert_live_state("sk1", None, status="queued",
                              plan={"tasks": [{"id": "T1"}]})
        before = board.get_live_state("sk1").updated_at
        board.upsert_live_state("sk1", before, status="active")
        entry_id = next(
            e.id for e in board.changelog()
            if e.action == "live state upsert" and e.mission_id is None
            and e.task_key is None
        )
        # the live-state audit keeps the mission_id as the state key in the
        # in-memory store; address it by the state key
        next(e for e in board.changelog() if e.id == entry_id)
        result = rollback(board, entry_id)
        assert result["restored"]["status"] == "queued"
        assert board.get_live_state("sk1").status == "queued"


# -------------------------------------------------------------------------- create


class TestCreateMission:
    def test_create_copies_config_rules_criteria_hints(self, board: InMemoryBoardStore):
        seed(board)
        result = create_mission(
            board, "m2", source_mission_id="m1", objective="do it",
            plan={"tasks": [
                {"id": "T1", "objective": "one", "status": "active", "attempt": 1}
            ]},
            who="me",
        )
        assert result["mission"]["mission_id"] == "m2"
        assert result["mission"]["rules"] == ["r1"]
        assert result["mission"]["acceptance_criteria"] == ["c1"]
        assert result["mission"]["retrieval_corpora"] == ["corpus"]
        # a copied plan keeps the task definitions but drops status and attempt
        assert result["live_state"]["plan"]["tasks"] == [
            {"id": "T1", "objective": "one"}
        ]
        # the config (with the hints/CLAUDE.md) is on its own row
        assert board.mission_config("m2") == {
            "required_files": ["/tmp/x/a.md"], "hints": "HINTS"
        }
        # all three rows are present; with no explicit position the new
        # mission lands at the end of the queue (the source mission keeps
        # its place in front of it)
        assert board.get_mission("m2") is not None
        assert board.get_live_state("m2") is not None
        assert [e.mission_id for e in board.queue()] == ["m1", "m2"]
        # the audit keeps the three rows together in one change
        entry = next(e for e in board.changelog() if e.action == "mission create")
        assert set(entry.after_json) == {"mission", "live_state", "queue"}
        # and the queue order is the same as the audit's queue snapshot
        assert [q["mission_id"] for q in entry.after_json["queue"]] == ["m1", "m2"]

    def test_create_from_a_template(self, board: InMemoryBoardStore):
        board.set_template("base", {"name": "Base", "rules": ["r"],
                                   "acceptance_criteria": ["c"],
                                   "config_json": {"required_files": ["/tmp/x/a.md"],
                                                   "hints": "H"},
                                   "working_directory": "/tmp/x"})
        result = create_mission(board, "m2", template_name="base",
                              objective="do it",
                              plan={"tasks": [{"id": "T1"}]}, who="me")
        assert result["mission"]["rules"] == ["r"]
        assert result["mission"]["acceptance_criteria"] == ["c"]
        assert board.mission_config("m2") == {
            "required_files": ["/tmp/x/a.md"], "hints": "H"
        }
        # the diff shows the fields that changed against the template
        # (the mission keeps the template's display name: no diff there)
        assert result["diff"] == {}

    def test_create_rejects_an_invalid_id(self, board: InMemoryBoardStore):
        with pytest.raises(MissionIdError):
            create_mission(board, "Bad Id")

    def test_create_rejects_a_duplicate_id(self, board: InMemoryBoardStore):
        seed(board)
        with pytest.raises(MissionIdError):
            create_mission(board, "m1", source_mission_id="m1")

    def test_create_refuses_a_running_source(self, board: InMemoryBoardStore):
        seed(board)
        board.upsert_live_state("sk1", None, status="active")
        with pytest.raises(ValueError, match="is running"):
            create_mission(board, "m2", source_mission_id="m1")

    def test_create_all_rows_or_none(self, board: InMemoryBoardStore, monkeypatch):
        """The rows land together: a failed config write leaves no trace of the
        new mission, and the other missions keep their exact prior state —
        the same positions, the same queue version, the same changelog."""
        seed(board)
        board._missions["m4"] = Mission(
            mission_id="m4", state_namespace="sk4", name="Other",
            working_directory="/tmp/y", rules=["r4"],
        )
        board._queue["m4"] = QueueEntry(position=1, mission_id="m4", enabled=True)
        board._queue_order.append("m4")
        previous = [(e.mission_id, e.position) for e in board.queue()]
        previous_version = board._queue_version
        previous_changelog = len(board.changelog())

        class FailingConfigDict(dict):
            """A config-table stand-in whose write fails (simulates a failed
            config row insert inside the atomic step).  It keeps the existing
            entries (so the source config is still copied) but refuses the
            new write."""

            def __setitem__(self, key, value):
                raise RuntimeError("simulated failure in the config write")

        # make the config write fail: the atomic step stores the config row
        # into ``_mission_config``; swapping in a dict that refuses the new
        # write (but keeps the existing rows) simulates the failure there.
        original_config = board._mission_config
        board._mission_config = FailingConfigDict(dict(original_config))
        try:
            with pytest.raises(RuntimeError):
                create_mission(board, "m2", source_mission_id="m1")
        finally:
            board._mission_config = original_config
        assert board.get_mission("m2") is None
        assert board.get_live_state("m2") is None
        assert board.mission_config("m2") is None
        assert all(e.mission_id != "m2" for e in board.queue())
        # the other missions are exactly as they were before the failed step
        assert [(e.mission_id, e.position) for e in board.queue()] == previous
        assert board._queue_version == previous_version
        assert len(board.changelog()) == previous_changelog

    def test_create_default_position_is_end_of_queue(self, board: InMemoryBoardStore):
        """With no ``position`` the new mission lands at the end of the queue."""
        seed(board)
        board._missions["m4"] = Mission(
            mission_id="m4", state_namespace="sk4", name="Other",
            working_directory="/tmp/y", rules=["r4"],
        )
        board._queue["m4"] = QueueEntry(position=1, mission_id="m4", enabled=True)
        board._queue_order.append("m4")
        create_mission(board, "m3", source_mission_id="m1")
        assert [e.mission_id for e in board.queue()] == ["m1", "m4", "m3"]
        assert [e.position for e in board.queue()] == [0, 1, 2]

    def test_create_uses_only_the_public_store_interface(self, monkeypatch):
        """create_mission must not touch the store's private attributes: a store
        that implements only the public interface (with the atomic
        create_mission_rows) is enough."""
        missions: dict[str, Mission] = {}
        live_states: dict[str, LiveState] = {}
        configs: dict[str, dict[str, Any]] = {}
        queue_entries: list[QueueEntry] = []

        class PublicOnlyStore:
            def __init__(self):
                self._tracked: dict[str, Any] = {}

            def get_mission(self, mission_id: str) -> Mission | None:
                return missions.get(mission_id)

            def get_live_state(self, state_key: str) -> LiveState | None:
                return live_states.get(state_key)

            def list_missions(self) -> list[Mission]:
                return list(missions.values())

            def mission_config(self, mission_id: str) -> dict[str, Any] | None:
                return configs.get(mission_id)

            def templates(self) -> list[dict[str, Any]]:
                return []

            def queue(self) -> list[QueueEntry]:
                return list(queue_entries)

            def create_mission_rows(
                self, mission, live, config, position=None, note=None, who="",
            ) -> None:
                # record that the atomic step was called, and record the
                # writes through the public views (as the store would)
                self._tracked["create_mission_rows"] = True
                missions[mission.mission_id] = mission
                live_states[live.state_key] = live
                if config:
                    configs[mission.mission_id] = dict(config)
                queue_entries.append(
                    QueueEntry(
                        position=len(queue_entries),
                        mission_id=mission.mission_id,
                        enabled=True,
                        note=note,
                    )
                )

        store = PublicOnlyStore()
        missions["m1"] = Mission(
            mission_id="m1", state_namespace="sk1", name="Source",
            working_directory="/tmp/x", rules=["r1"],
            acceptance_criteria=["c1"], retrieval_corpora=["corpus"],
        )
        configs["m1"] = {"required_files": ["/tmp/x/a.md"], "hints": "HINTS"}

        result = create_mission(
            store, "m2", source_mission_id="m1", objective="do it"
        )
        # the atomic method was used, and the public views show the new rows
        assert store._tracked.get("create_mission_rows") is True
        assert result["mission"]["mission_id"] == "m2"
        assert store.get_mission("m2") is not None
        assert store.get_live_state("m2") is not None
        assert store.mission_config("m2") == {
            "required_files": ["/tmp/x/a.md"], "hints": "HINTS"
        }
        assert [e.mission_id for e in store.queue()] == ["m2"]


# -------------------------------------------------------------------------- template


class TestSaveTemplate:
    def test_save_template_drops_objective_and_plan(self, board: InMemoryBoardStore):
        seed(board)
        template = save_template(board, "m1", "base", who="me")
        # the stored row: the name, the source mission id, the config dict
        # (rules, criteria, corpora, working folder, hints inside it)
        assert template["name"] == "base"
        assert template["source_mission_id"] == "m1"
        assert "objective" not in template
        assert "plan" not in template
        cfg = template["config_json"]
        assert cfg["rules"] == ["r1"]
        assert cfg["acceptance_criteria"] == ["c1"]
        assert cfg["working_directory"] == "/tmp/x"
        assert cfg["config_json"] == {"required_files": ["/tmp/x/a.md"], "hints": "HINTS"}
        # the template row is usable for creation
        result = create_mission(board, "m2", template_name="base",
                              objective="do it",
                              plan={"tasks": [{"id": "T1"}]})
        assert result["mission"]["rules"] == ["r1"]

    def test_save_template_refuses_an_unknown_mission(self, board: InMemoryBoardStore):
        with pytest.raises(KeyError):
            save_template(board, "nope", "base")


# -------------------------------------------------------------------------- readiness


class TestCollectReadiness:
    def test_collect_readiness_reports_each_item(self, board: InMemoryBoardStore):
        seed(board)
        # no Live State row yet: the mission is not ready
        summary = asyncio.run(collect_readiness(board, board.get_mission("m1")))
        checks = {i["check"] for i in summary["items"]}
        assert checks == {
            "working_folder", "required_files", "runner", "litellm",
            "llama_model", "plan", "live_state", "not_running", "preflight",
        }
        assert summary["status"] == "error"
        assert summary["confirmed_needed"] is True
        # the working folder is set but does not exist on the runner
        assert next(i for i in summary["items"]
                    if i["check"] == "working_folder")["status"] == "error"
        # the guard blocks on the errors
        assert start_guard(summary)["allowed"] is False

    def test_a_fully_ready_mission_is_ok(self, board: InMemoryBoardStore, tmp_path):
        workdir = tmp_path / "mission"
        workdir.mkdir()
        (workdir / "a.md").write_text("x")
        seed(board)
        board._missions["m1"].working_directory = str(workdir)
        board.set_mission_config(
            "m1", {"required_files": [str(workdir / "a.md")]}, None
        )
        board.upsert_live_state(
            "sk1", None, plan={"tasks": [{"id": "T1"}]}, status="queued"
        )
        board.record_preflight("m1", [{"check": "ok"}])
        summary = asyncio.run(collect_readiness(board, board.get_mission("m1")))
        # without settings the URL checks are warnings, the rest are ok
        assert summary["status"] == "warning"
        assert all(
            i["status"] != "error" for i in summary["items"]
            if i["check"] in ("working_folder", "required_files",
                             "plan", "live_state", "not_running", "preflight")
        )
        assert start_guard(summary)["allowed"] is True
        # a confirmed start is allowed even with warnings
        assert start_guard(summary, confirmed=True)["allowed"] is True
