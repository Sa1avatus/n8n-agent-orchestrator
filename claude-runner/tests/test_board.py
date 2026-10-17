"""Tests for InMemoryBoardStore (claude_runner/board.py): the board reads and
grouping, queue reordering with contiguous positions, the refusal to touch a
running mission, the optimistic lock (409), the read-only task guard, the
config write with a diff, the Worker hint composition, and the unavailable
store when the backend is missing — plus the plan-edit functions (add /
delete / move / duplicate / hint / retry), consistent mission creation, the
changelog written before every change, and the 503 API response when the
store is unavailable.  T015 adds the backend stats (time, tokens, cache,
thinking on/off, cost, speed) over synthetic runs, the run-to-task linkage
through the event log, the readiness checks each on their own, the start
guard blocked by errors, the rollback and its conflict, and the backup row
saved before every change."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from claude_runner.board import (
    ConflictError,
    GuardError,
    InMemoryBoardStore,
    LiveState,
    Mission,
    QueueEntry,
)
from claude_runner.board_pg import UnavailableBoardStore, get_board_store
from claude_runner.board_plan import (
    add_task,
    compose_task_text,
    delete_task,
    duplicate_task,
    move_task,
    request_retry,
    set_task_hint,
    update_mission_config,
    update_task,
)
from claude_runner.board_ready import (
    MissionIdError,
    check_live_state_present,
    check_not_running,
    check_plan_present,
    check_preflight_fresh,
    check_required_files,
    check_working_folder,
    collect_readiness,
    create_mission,
    rollback,
    start_guard,
    validate_mission_id,
)
from claude_runner.board_stats import mission_card_stats, task_stats
from claude_runner.config import Settings
from claude_runner.events import EventLog
from claude_runner.runs import RunManager
from claude_runner.store import Store


@pytest.fixture
def board() -> InMemoryBoardStore:
    """A fresh in-memory store (no seed: put rows in per test)."""
    return InMemoryBoardStore()


def _mission(mission_id: str, state_namespace: str, enabled: bool = True) -> Mission:
    return Mission(mission_id=mission_id, state_namespace=state_namespace, enabled=enabled)


class TestBoardReads:
    def test_list_missions_is_sorted(self, board: InMemoryBoardStore):
        board._missions["m2"] = _mission("m2", "sk2")
        board._missions["m1"] = _mission("m1", "sk1")
        missions = board.list_missions()
        assert [m.mission_id for m in missions] == ["m1", "m2"]

    def test_get_mission_and_list_live_states(self, board: InMemoryBoardStore):
        board._missions["m1"] = _mission("m1", "sk1")
        board._missions["m2"] = _mission("m2", "sk2")
        board._live["sk1"] = LiveState(state_key="sk1", status="active")
        assert board.get_mission("m1") is not None
        assert board.get_mission("nope") is None
        live_states = board.list_live_states()
        assert [s.state_key for s in live_states] == ["sk1"]

    def test_running_missions_selects_only_active(self, board: InMemoryBoardStore):
        board._missions["m1"] = _mission("m1", "sk1")
        board._missions["m2"] = _mission("m2", "sk2")
        board._live["sk1"] = LiveState(state_key="sk1", status="active")
        board._live["sk2"] = LiveState(state_key="sk2", status="completed")
        assert [m.mission_id for m in board.running_missions()] == ["m1"]


class TestMissionGroups:
    def test_groups_split_running_queued_completed_not_completed_disabled(self,
                                                                       board: InMemoryBoardStore):
        # running: enabled + Live State active
        board._missions["m1"] = _mission("m1", "sk1")
        board._live["sk1"] = LiveState(state_key="sk1", status="active")
        # completed: enabled + completed
        board._missions["m2"] = _mission("m2", "sk2")
        board._live["sk2"] = LiveState(state_key="sk2", status="completed")
        # not_completed: enabled + stopped
        board._missions["m3"] = _mission("m3", "sk3")
        board._live["sk3"] = LiveState(state_key="sk3", status="stopped")
        # queued: enabled, no Live State row
        board._missions["m4"] = _mission("m4", "sk4")
        # disabled: enabled is False (wins over any status)
        board._missions["m5"] = _mission("m5", "sk5", enabled=False)
        groups = board.mission_groups()
        assert groups["running"] == ["m1"]
        assert groups["queued"] == ["m4"]
        assert groups["completed"] == ["m2"]
        assert groups["not_completed"] == ["m3"]
        assert groups["disabled"] == ["m5"]

    def test_failed_is_not_completed(self, board: InMemoryBoardStore):
        board._missions["m1"] = _mission("m1", "sk1")
        board._live["sk1"] = LiveState(state_key="sk1", status="failed")
        assert board.mission_groups()["not_completed"] == ["m1"]


class TestQueue:
    def test_move_queue_reorders_and_renumbers_contiguously(self, board: InMemoryBoardStore):
        board._queue["m1"] = QueueEntry(position=0, mission_id="m1")
        board._queue["m2"] = QueueEntry(position=1, mission_id="m2")
        board._queue["m3"] = QueueEntry(position=2, mission_id="m3")
        board._queue_order = ["m1", "m2", "m3"]
        moved = board.move_queue("m3", 0, None)
        assert [e.mission_id for e in moved] == ["m3", "m1", "m2"]
        assert [e.position for e in moved] == [0, 1, 2]

    def test_move_queue_keeps_the_moved_mission_fields(self, board: InMemoryBoardStore):
        board._queue["m1"] = QueueEntry(position=0, mission_id="m1",
                                        note="first", attempts_per_task=5.0)
        board._queue["m2"] = QueueEntry(position=1, mission_id="m2")
        board._queue_order = ["m1", "m2"]
        moved = board.move_queue("m1", 1, None)
        entry = moved[1]
        assert entry.mission_id == "m1"
        assert entry.note == "first"
        assert entry.attempts_per_task == 5.0

    def test_set_queue_renumbers_and_rejects_duplicates(self, board: InMemoryBoardStore):
        board._queue["m1"] = QueueEntry(position=0, mission_id="m1")
        board._queue["m2"] = QueueEntry(position=1, mission_id="m2")
        board._queue_order = ["m1", "m2"]
        result = board.set_queue(
            [QueueEntry(position=9, mission_id="m2"),
             QueueEntry(position=10, mission_id="m1")],
            None,
        )
        # positions are renumbered contiguously in the given order
        assert [(e.position, e.mission_id) for e in result] == [(0, "m2"), (1, "m1")]
        with pytest.raises(ValueError):
            board.set_queue(
                [QueueEntry(position=0, mission_id="m1"),
                 QueueEntry(position=1, mission_id="m1")],
                None,
            )

    def test_add_queue_appends_and_renumbers(self, board: InMemoryBoardStore):
        board._queue["m1"] = QueueEntry(position=0, mission_id="m1")
        board._queue_order = ["m1"]
        result = board.add_queue("m2", None, position=0)
        assert [(e.position, e.mission_id) for e in result] == [(0, "m2"), (1, "m1")]
        with pytest.raises(ValueError):
            board.add_queue("m1", None)  # already in the queue

    def test_remove_queue_renumbers(self, board: InMemoryBoardStore):
        board._queue["m1"] = QueueEntry(position=0, mission_id="m1")
        board._queue["m2"] = QueueEntry(position=1, mission_id="m2")
        board._queue_order = ["m1", "m2"]
        result = board.remove_queue("m1", None)
        assert [(e.position, e.mission_id) for e in result] == [(0, "m2")]
        with pytest.raises(KeyError):
            board.remove_queue("nope", None)


class TestRunningGuard:
    def _make_running(self, board: InMemoryBoardStore, mission_id: str) -> None:
        board._missions[mission_id] = _mission(mission_id, mission_id)
        board._live[mission_id] = LiveState(state_key=mission_id, status="active")

    def test_move_queue_refuses_a_running_mission(self, board: InMemoryBoardStore):
        self._make_running(board, "m1")
        board._queue["m1"] = QueueEntry(position=0, mission_id="m1")
        board._queue_order = ["m1"]
        with pytest.raises(GuardError):
            board.move_queue("m1", 1, None)

    def test_remove_queue_refuses_a_running_mission(self, board: InMemoryBoardStore):
        self._make_running(board, "m1")
        board._queue["m1"] = QueueEntry(position=0, mission_id="m1")
        board._queue_order = ["m1"]
        with pytest.raises(GuardError):
            board.remove_queue("m1", None)

    def test_set_queue_refuses_a_running_mission(self, board: InMemoryBoardStore):
        self._make_running(board, "m1")
        board._queue["m1"] = QueueEntry(position=0, mission_id="m1")
        board._queue_order = ["m1"]
        with pytest.raises(GuardError):
            board.set_queue([QueueEntry(position=0, mission_id="m1")], None)

    def test_add_queue_refuses_a_running_mission(self, board: InMemoryBoardStore):
        self._make_running(board, "m1")
        with pytest.raises(GuardError):
            board.add_queue("m1", None)


class TestOptimisticLock:
    def test_update_mission_with_stale_version_raises_conflict(self, board: InMemoryBoardStore):
        board._missions["m1"] = _mission("m1", "sk1")
        first = board.update_mission("m1", None, objective="v1")
        # the stored version is now first.updated_at; a stale version must conflict
        with pytest.raises(ConflictError):
            board.update_mission("m1", "stale-version", objective="v2")
        # the correct version is accepted
        updated = board.update_mission("m1", first.updated_at, objective="v2")
        assert updated.objective == "v2"

    def test_upsert_live_state_with_stale_version_raises_conflict(self,
                                                                  board: InMemoryBoardStore):
        board.upsert_live_state("sk1", None, status="queued")
        current = board.get_live_state("sk1")
        with pytest.raises(ConflictError):
            board.upsert_live_state("sk1", "stale-version", status="active")
        live = board.upsert_live_state("sk1", current.updated_at, status="active")
        assert live.status == "active"


class TestTaskEditGuard:
    def _seed(self, board: InMemoryBoardStore, status: str, task_index: int,
             plan: dict, events: list[dict] | None = None):
        """Put a Live State row (stamping a version) and optional events in."""
        board.upsert_live_state("sk1", None, plan=plan, status=status,
                               task_index=task_index, task_attempt=1)
        for event in events or []:
            board.append_event("sk1", event)
        live = board.get_live_state("sk1")
        return live.plan, live.updated_at, list(events or [])

    def test_update_task_refuses_an_accepted_task(self, board: InMemoryBoardStore):
        plan, stored, events = self._seed(
            board, "active", 1,
            {"tasks": [{"id": "T001", "title": "t", "objective": "o"},
                       {"id": "T002", "title": "t2", "objective": "o2"}]},
            events=[{"task_id": "T001", "event_type": "review_parsed",
                    "review_status": "pass", "review_score": 5}],
        )
        # task 0 is past the live index 1 and its review passed -> accepted
        with pytest.raises(GuardError):
            update_task(plan, stored, stored, "T001", "active", 1, 1,
                        title="new title", events=events)

    def test_update_task_refuses_a_running_task(self, board: InMemoryBoardStore):
        plan, stored, events = self._seed(
            board, "active", 0,
            {"tasks": [{"id": "T001", "title": "t", "objective": "o"}]},
        )
        # task 0 is the live task with status active -> running
        with pytest.raises(GuardError):
            update_task(plan, stored, stored, "T001", "active", 0, 1,
                        title="new title", events=events)

    def test_update_task_allows_a_waiting_task(self, board: InMemoryBoardStore):
        plan, stored, events = self._seed(
            board, "active", 0,
            {"tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                       {"id": "T002", "title": "two", "objective": "o2"}]},
        )
        # task 1 is past the live index 0 -> waiting and editable
        new_plan, payload = update_task(plan, stored, stored, "T002", "active", 0, 1,
                                        title="two (edited)", events=events)
        assert new_plan["tasks"][1]["title"] == "two (edited)"
        assert payload["action"] == "task update"
        assert payload["task_key"] == "T002"

    def test_update_task_stale_version_raises_conflict(self, board: InMemoryBoardStore):
        plan, stored, events = self._seed(
            board, "active", 0,
            {"tasks": [{"id": "T001", "title": "one", "objective": "o1"}]},
        )
        with pytest.raises(ConflictError):
            update_task(plan, "stale", stored, "T001", "active", 0, 1,
                        title="x", events=events)


class TestMissionConfig:
    def test_update_mission_config_applies_fields_and_builds_a_diff(self,
                                                                    board: InMemoryBoardStore):
        config = {"hints": "old", "required_files": ["/a"]}
        new_config, payload = update_mission_config(
            config, "v1", "v1", mission_running=False, hints="new",
        )
        assert new_config["hints"] == "new"
        assert new_config["required_files"] == ["/a"]
        assert payload["action"] == "mission config update"
        # the changelog payload carries the changed row (the diff vs the old config)
        assert payload["plan"]["hints"] == "new"
        assert payload["plan"]["required_files"] == ["/a"]

    def test_update_mission_config_refuses_non_midrun_fields_while_running(self,
                                                                         board: InMemoryBoardStore):
        with pytest.raises(GuardError):
            update_mission_config(
                {"working_directory": "/x"}, "v1", "v1",
                mission_running=True, working_directory="/y",
            )
        # a midrun field is allowed while running
        config, _ = update_mission_config(
            {"max_tasks": 5}, "v1", "v1", mission_running=True, max_tasks=7,
        )
        assert config["max_tasks"] == 7

    def test_set_mission_config_records_before_and_after_in_changelog(self,
                                                                     board: InMemoryBoardStore):
        board.set_mission_config("m1", {"hints": "first"}, None)
        first_version = board._mission_config_version["m1"]
        board.set_mission_config("m1", {"hints": "second"}, first_version)
        entry = next(
            e for e in board.changelog() if e.action == "mission_config set"
        )
        assert entry.before_json is not None
        assert entry.before_json["config_json"]["hints"] == "first"
        assert entry.after_json["config_json"]["hints"] == "second"
        assert board.mission_config("m1") == {"hints": "second"}


class TestWorkerHintComposition:
    def test_compose_task_text_is_title_objective_criteria(self):
        task = {
            "title": "Build it",
            "objective": "Make it work",
            "acceptance_criteria": ["runs", "tested"],
        }
        assert compose_task_text(task) == (
            "Build it\n\nMake it work\n\n"
            "Acceptance criteria:\nruns\ntested"
        )

    def test_compose_task_text_appends_the_worker_hint(self):
        task = {"title": "Build it", "objective": "Make it work"}
        assert compose_task_text(task, hint="use module X") == (
            "Build it\n\nMake it work\n\nWorker hint:\nuse module X"
        )

    def test_compose_task_text_ignores_blank_and_none_hints(self):
        task = {"title": "Build it", "objective": "Make it work"}
        assert compose_task_text(task, hint=None) == "Build it\n\nMake it work"
        assert compose_task_text(task, hint="   ") == "Build it\n\nMake it work"


class TestUnavailableStore:
    def test_status_reports_unavailable_without_leaking_the_dsn(self):
        store = UnavailableBoardStore("DSN is not set")
        status, reason = store.status()
        assert status == "unavailable"
        assert "DSN" in reason
        # the reason is a summary, never the DSN itself
        assert "postgresql://" not in reason

    def test_get_board_store_returns_unavailable_when_no_dsn(self):
        store = get_board_store(None)
        assert isinstance(store, UnavailableBoardStore)
        status, reason = store.status()
        assert status == "unavailable"
        assert "postgresql://" not in reason

    def test_reads_refuse_with_a_runtime_error(self):
        store = UnavailableBoardStore("DSN is not set")
        with pytest.raises(RuntimeError):
            store.list_missions()
        with pytest.raises(RuntimeError):
            store.queue()
        with pytest.raises(RuntimeError):
            store.changelog()


class _TaskEditMixin:
    """Shared seeding: a mission plus one Live State row (stamped with a
    version) and optional event rows, from which the task statuses derive."""

    def _seed(self, board: InMemoryBoardStore, plan: dict,
              live_status: str = "active", task_index: int = 0,
              task_attempt: int = 1, events: list[dict] | None = None):
        board._missions["m1"] = _mission("m1", "sk1")
        board.upsert_live_state("sk1", None, plan=plan, status=live_status,
                               task_index=task_index, task_attempt=task_attempt)
        for event in events or []:
            board.append_event("sk1", event)
        live = board.get_live_state("sk1")
        return live.plan, live.updated_at, list(events or [])


class TestAddDeleteMoveDuplicate(_TaskEditMixin):

    def test_add_task_appends_and_renumbers_keys(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(board, {
            "tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                      {"id": "T002", "title": "two", "objective": "o2"}],
        })
        new_plan, payload = add_task(plan, stored, stored, title="three",
                                    objective="o3", live_status="active",
                                    live_task_index=0)
        assert [t["id"] for t in new_plan["tasks"]] == ["T001", "T002", "T003"]
        assert new_plan["tasks"][2]["title"] == "three"
        assert payload["action"] == "task add"
        assert payload["task_key"] == "T003"

    def test_add_task_inserts_in_the_middle_and_renumbers(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(board, {
            "tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                      {"id": "T002", "title": "two", "objective": "o2"}],
        })
        new_plan, _ = add_task(plan, stored, stored, title="new",
                              after_key="T001", live_status="active",
                              live_task_index=0)
        assert [t["id"] for t in new_plan["tasks"]] == ["T001", "T002", "T003"]
        assert new_plan["tasks"][1]["title"] == "new"

    def test_add_task_refuses_an_accepted_task(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(
            board,
            {"tasks": [{"id": "T001", "title": "one", "objective": "o1"}]},
            live_status="active", task_index=1,
            events=[{"task_id": "T001", "event_type": "review_parsed",
                    "review_status": "pass", "review_score": 5}],
        )
        # inserting before the live index would land at/before it: refused
        with pytest.raises(GuardError):
            add_task(plan, stored, stored, title="x", after_key="T001",
                     live_status="active", live_task_index=1)

    def test_add_task_refuses_an_insert_at_or_before_the_live_index(self,
                                                                   board: InMemoryBoardStore):
        plan, stored, _ = self._seed(board, {
            "tasks": [{"id": "T001", "title": "one", "objective": "o1"}],
        }, task_index=1,
            events=[{"task_id": "T001", "event_type": "review_parsed",
                    "review_status": "pass", "review_score": 5}])
        # T001 is accepted (index 0 < live index 1, review passed); inserting
        # after it would land at the live index: refused
        with pytest.raises(GuardError):
            add_task(plan, stored, stored, title="x", after_key="T001",
                     live_status="active", live_task_index=1)

    def test_delete_task_removes_and_renumbers_keys(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(board, {
            "tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                      {"id": "T002", "title": "two", "objective": "o2"},
                      {"id": "T003", "title": "three", "objective": "o3"}],
        })
        new_plan, payload = delete_task(plan, stored, stored, "T002",
                                       "active", 0)
        assert [t["id"] for t in new_plan["tasks"]] == ["T001", "T002"]
        assert new_plan["tasks"][1]["title"] == "three"
        assert payload["action"] == "task delete"

    def test_delete_task_refuses_an_accepted_task(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(
            board,
            {"tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                       {"id": "T002", "title": "two", "objective": "o2"}]},
            live_status="active", task_index=1,
            events=[{"task_id": "T001", "event_type": "review_parsed",
                    "review_status": "pass", "review_score": 5}],
        )
        # task T001 is accepted (index 0 < live index 1, review passed)
        with pytest.raises(GuardError):
            delete_task(plan, stored, stored, "T001", "active", 1)

    def test_delete_task_refuses_a_running_task(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(board, {
            "tasks": [{"id": "T001", "title": "one", "objective": "o1"}],
        })
        # T001 is the live task (index 0 == live index 0) -> running
        with pytest.raises(GuardError):
            delete_task(plan, stored, stored, "T001", "active", 0)

    def test_move_task_reorders_and_renumbers_keys(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(board, {
            "tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                      {"id": "T002", "title": "two", "objective": "o2"},
                      {"id": "T003", "title": "three", "objective": "o3"}],
        })
        new_plan, payload = move_task(plan, stored, stored, "T003", 1,
                                     live_status="active", live_task_index=0)
        # the task (title "three") is now at position 1; keys renumber T001..T003
        assert [t["title"] for t in new_plan["tasks"]] == ["one", "three", "two"]
        assert [t["id"] for t in new_plan["tasks"]] == ["T001", "T002", "T003"]
        # the moved task now carries key T002
        assert new_plan["tasks"][1] == {
            "id": "T002", "title": "three", "objective": "o3",
            "acceptance_criteria": [], "verification": [], "scope": [],
            "dependencies": [],
        }
        assert payload["action"] == "task move"
        assert payload["from_index"] == 2
        assert payload["to_position"] == 1

    def test_move_task_refuses_an_accepted_task(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(
            board,
            {"tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                       {"id": "T002", "title": "two", "objective": "o2"}]},
            live_status="active", task_index=1,
            events=[{"task_id": "T001", "event_type": "review_parsed",
                    "review_status": "pass", "review_score": 5}],
        )
        # T001 is accepted; it cannot be moved
        with pytest.raises(GuardError):
            move_task(plan, stored, stored, "T001", 1, "active", 1)

    def test_move_task_refuses_a_running_task(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(board, {
            "tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                      {"id": "T002", "title": "two", "objective": "o2"}],
        })
        # T001 is the live task; and the target position is past the live index
        with pytest.raises(GuardError):
            move_task(plan, stored, stored, "T001", 1, "active", 0)

    def test_duplicate_task_dups_and_renumbers_keys(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(board, {
            "tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                      {"id": "T002", "title": "two", "objective": "o2"}],
        })
        new_plan, payload = duplicate_task(plan, stored, stored, "T002",
                                          live_status="active", live_task_index=0)
        # T002 is waiting (index 1 > live index 0); its copy lands as T003
        assert [t["id"] for t in new_plan["tasks"]] == ["T001", "T002", "T003"]
        assert new_plan["tasks"][2]["title"] == "two"
        assert payload["action"] == "task duplicate"
        assert payload["task_key"] == "T003"

    def test_duplicate_task_refuses_an_accepted_task(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(
            board,
            {"tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                       {"id": "T002", "title": "two", "objective": "o2"}]},
            live_status="active", task_index=1,
            events=[{"task_id": "T001", "event_type": "review_parsed",
                    "review_status": "pass", "review_score": 5}],
        )
        # T001 is accepted (index 0 < live index 1, review passed); duplicating
        # a non-pending task is refused
        with pytest.raises(GuardError):
            duplicate_task(plan, stored, stored, "T001",
                          live_status="active", live_task_index=1)

    def test_duplicate_task_refuses_a_running_task(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(board, {
            "tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                      {"id": "T002", "title": "two", "objective": "o2"}],
        })
        # T001 is the live task (index 0 == live index 0); duplicating it is
        # refused
        with pytest.raises(GuardError):
            duplicate_task(plan, stored, stored, "T001",
                          live_status="active", live_task_index=0)


class TestTaskHintAndRetry(_TaskEditMixin):

    def test_set_task_hint_stores_the_override_for_a_waiting_task(self,
                                                                  board: InMemoryBoardStore):
        plan, stored, _ = self._seed(board, {
            "tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                      {"id": "T002", "title": "two", "objective": "o2"}],
        })
        _, payload = set_task_hint(plan, stored, stored, "T002",
                                  "use module X", "active", 0, 1)
        assert payload["action"] == "task hint set"
        assert payload["override"] == {"task_key": "T002", "hint": "use module X"}

    def test_set_task_hint_refuses_an_accepted_task(self, board: InMemoryBoardStore):
        plan, stored, events = self._seed(
            board,
            {"tasks": [{"id": "T001", "title": "one", "objective": "o1"}]},
            live_status="active", task_index=1,
            events=[{"task_id": "T001", "event_type": "review_parsed",
                    "review_status": "pass", "review_score": 5}],
        )
        with pytest.raises(GuardError):
            set_task_hint(plan, stored, stored, "T001", "hint",
                         live_status="active", live_task_index=1,
                         live_task_attempt=1, events=events)

    def test_set_task_hint_refuses_a_running_task(self, board: InMemoryBoardStore):
        plan, stored, _ = self._seed(board, {
            "tasks": [{"id": "T001", "title": "one", "objective": "o1"}],
        })
        with pytest.raises(GuardError):
            set_task_hint(plan, stored, stored, "T001", "hint",
                         "active", 0, 1)

    def test_request_retry_resets_the_attempt_counter_to_one(self,
                                                            board: InMemoryBoardStore):
        plan, stored, _ = self._seed(
            board,
            {"tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                       {"id": "T002", "title": "two", "objective": "o2"}]},
            live_status="active", task_index=1, task_attempt=3,
            events=[{"task_id": "T001", "event_type": "review_parsed",
                    "review_status": "needs_changes"}],
        )
        # T001 is not_accepted (index 0 < live index 1, review needs_changes)
        _, payload = request_retry(plan, stored, stored, "T001",
                                  "active", 1, 3)
        assert payload["action"] == "task retry requested"
        assert payload["live_state"] == {"task_attempt": 1}
        assert payload["previous_attempt"] == 3

    def test_request_retry_refuses_a_not_accepted_only(self,
                                                      board: InMemoryBoardStore):
        plan, stored, _ = self._seed(board, {
            "tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                      {"id": "T002", "title": "two", "objective": "o2"}],
        })
        # T002 is waiting and cannot be retried
        with pytest.raises(GuardError):
            request_retry(plan, stored, stored, "T002", "active", 0, 1)


class TestMissionCreation:
    def _source_mission(self, board: InMemoryBoardStore) -> None:
        board._missions["m1"] = _mission("m1", "sk1")
        board.set_mission_config("m1", {"required_files": ["/w/a.md"],
                                        "hints": "HINTS"}, None)
        board._queue["m1"] = QueueEntry(position=0, mission_id="m1")
        board._queue_order.append("m1")
        board.upsert_live_state("sk1", None, status="queued",
                               plan={"tasks": [{"id": "T001", "objective": "one"}]})

    def test_create_mission_lands_consistent_rows(self, board: InMemoryBoardStore):
        self._source_mission(board)
        result = create_mission(board, "new-mission", source_mission_id="m1",
                               objective="build it",
                               plan={"tasks": [{"id": "T1", "objective": "one",
                                               "status": "running",
                                               "attempt": 3}]})
        result["mission"]
        result["live_state"]
        assert board.get_mission("new-mission") is not None
        assert board.get_mission("new-mission").objective == "build it"
        assert board.get_live_state("new-mission").plan == {
            "summary": "", "tasks": [{"id": "T1", "objective": "one"}]
        }
        queue = board.queue()
        assert [e.mission_id for e in queue] == ["m1", "new-mission"]
        assert queue[1].position == 1
        # the copied plan strips statuses and attempts (a live run, not a template)
        live_plan = board.get_live_state("new-mission").plan
        assert live_plan == {"summary": "", "tasks": [{"id": "T1", "objective": "one"}]}
        # the copied config (required files + hints) lands with the new mission
        assert board.mission_config("new-mission") == {
            "required_files": ["/w/a.md"], "hints": "HINTS"}

    def test_create_mission_rejects_an_invalid_mission_id(self,
                                                          board: InMemoryBoardStore):
        self._source_mission(board)
        with pytest.raises(MissionIdError):
            create_mission(board, "bad id!", source_mission_id="m1")
        # and an empty id
        with pytest.raises(MissionIdError):
            create_mission(board, "", source_mission_id="m1")

    def test_create_mission_rejects_a_duplicate_mission_id(self,
                                                           board: InMemoryBoardStore):
        self._source_mission(board)
        with pytest.raises(MissionIdError):
            create_mission(board, "m1", source_mission_id="m1")
        # a new id that is already a state namespace is also refused
        with pytest.raises(MissionIdError):
            create_mission(board, "sk1", source_mission_id="m1")

    def test_validate_mission_id_rejects_non_slug_ids(self, board: InMemoryBoardStore):
        with pytest.raises(MissionIdError):
            validate_mission_id(board, "bad id!")
        with pytest.raises(MissionIdError):
            validate_mission_id(board, "UPPER")
        with pytest.raises(MissionIdError):
            validate_mission_id(board, "")
        # a valid, unused slug is accepted
        assert validate_mission_id(board, "good_slug-1") == "good_slug-1"


class TestChangelogOrder:
    def test_changelog_entry_is_written_before_the_change(self,
                                                          board: InMemoryBoardStore):
        board._missions["m1"] = _mission("m1", "sk1")
        board._missions["m2"] = _mission("m2", "sk2")
        board._queue["m1"] = QueueEntry(position=0, mission_id="m1")
        board._queue["m2"] = QueueEntry(position=1, mission_id="m2")
        board._queue_order = ["m1", "m2"]
        before_queue = [dict(e.model_dump()) for e in board.queue()]
        board.move_queue("m2", 0, None)
        entries = board.changelog()  # newest first
        entry = next(e for e in entries if e.action == "queue move")
        assert entry.before_json == before_queue
        assert entry.after_json[0]["mission_id"] == "m2"

    def test_update_mission_records_before_and_after(self,
                                                    board: InMemoryBoardStore):
        board._missions["m1"] = _mission("m1", "sk1")
        before = board.get_mission("m1").model_dump()
        board.update_mission("m1", None, objective="new")
        entry = next(e for e in board.changelog() if e.action == "mission update")
        assert entry.before_json == before
        assert entry.after_json["objective"] == "new"
        assert entry.mission_id == "m1"

    def test_task_edit_records_before_and_after(self, board: InMemoryBoardStore):
        board._missions["m1"] = _mission("m1", "sk1")
        plan = {"tasks": [{"id": "T001", "title": "one", "objective": "o1"},
                         {"id": "T002", "title": "two", "objective": "o2"}]}
        board.upsert_live_state("sk1", None, plan=plan, status="active",
                               task_index=0, task_attempt=1)
        live = board.get_live_state("sk1")
        before_plan = live.plan
        new_plan, _ = update_task(live.plan, live.updated_at, live.updated_at,
                                 "T002", live_status="active", live_task_index=0,
                                 live_task_attempt=1, title="two (edited)")
        # the API persists the edited plan via upsert_live_state
        board.upsert_live_state("sk1", None, plan=new_plan)
        # the newest "live state upsert" entry is the task-edit write
        entry = next(e for e in board.changelog() if e.action == "live state upsert")
        assert entry.before_json["plan"] == before_plan
        assert entry.after_json["plan"]["tasks"][1]["title"] == "two (edited)"


class TestUnavailableStoreApi:
    def test_status_is_503_with_a_readable_reason(self, settings):
        """With no DSN the board status route answers 503 and the reason
        never carries the DSN itself (a readable summary instead)."""
        from fastapi.testclient import TestClient

        from claude_runner.api import create_app
        app = create_app(settings)
        app.state.board_store = UnavailableBoardStore("DSN is not set")
        with TestClient(app) as client:
            r = client.get("/api/board/status")
            assert r.status_code == 503
            body = r.json()
            assert body["code"] == "unavailable"
            # the reason is a summary, never the DSN itself
            assert "DSN" in body["reason"]
            assert "postgresql://" not in body["reason"]


# -------------------------------------------------------------------------- T015: stats over synthetic runs


def _seed_mission(board: InMemoryBoardStore, mission_id: str, state_key: str) -> None:
    """The store updates rows but has no create: put the mission row in directly."""
    board._missions[mission_id] = Mission(
        mission_id=mission_id, state_namespace=state_key, name="Mission",
    )
    board.upsert_live_state(
        state_key, None,
        plan={"tasks": [{"id": "T001"}, {"id": "T002"}]}, status="queued",
    )


def _write_run_events(log: EventLog, run_id: str, entries: list[dict]) -> None:
    """Write a run's event log (the entries the stats read)."""
    log.open(run_id)
    for entry in entries:
        log.add(run_id, entry)
    log.close(run_id)


@pytest.fixture
def runs_store(settings) -> Store:
    return Store(settings.data_dir / "board_stats.sqlite")


@pytest.fixture
def run_manager(settings, runs_store) -> RunManager:
    env = {
        "CLAUDE_RUNNER_DATA_DIR": str(settings.data_dir),
        "CLAUDE_RUNNER_CLAUDE_BIN": settings.claude_bin,
        "CLAUDE_RUNNER_WORKSPACE": str(settings.workspace),
        "CLAUDE_RUNNER_PATH_MAP": ";".join(
            f"{src_path}={dst_path}" for src_path, dst_path in settings.path_map
        ),
        "CLAUDE_RUNNER_MAX_CONCURRENT": "2",
        "CLAUDE_RUNNER_COMPACT_MIN_TOKENS": "2500",
        "CLAUDE_RUNNER_LOCAL_PRICE_SONNET": "2,10,0.2,4",
        "CLAUDE_RUNNER_LOCAL_PRICE_HAIKU": "1,5,0.1,1.25",
        "CLAUDE_RUNNER_LOCAL_PRICE_THINKING_OFF": "haiku",
        "CLAUDE_RUNNER_LOCAL_PRICE_THINKING_ON": "sonnet",
    }
    return RunManager(Settings.from_env(env), runs_store)


def _add_run(
    store: Store, run_id: str, session_id: str, role: str,
    started: datetime, finished: datetime | None,
    cost_usd: float, provider: str = "", details: dict | None = None,
    kind: str = "run",
) -> None:
    store.create_run(
        run_id=run_id, session_id=session_id, claude_session_id=run_id,
        role=role, model="m", provider=provider, cwd="/tmp", input="x",
        kind=kind, status="queued", cost_usd=cost_usd,
        details=json.dumps(details or {}),
        started_at=started.isoformat(),
        finished_at=finished.isoformat() if finished else None,
    )
    if finished:
        store.update_run(run_id, status="completed")


def _event(state_key: str, **fields) -> dict:
    row = {"state_key": state_key, "created_at": "2026-01-01T00:00:00+00:00"}
    row.update(fields)
    return row


class TestBoardStats:
    """The mission card's aggregates over synthetic runs: time, tokens,
    cache, thinking on/off, cost (local and provider), speed."""

    def _seed_board(self, board: InMemoryBoardStore) -> None:
        _seed_mission(board, "m1", "sk1")
        board.update_hermes_config(None, profile_id="claude-code",
                                  max_attempts_per_task=2)

    def _link(self, board: InMemoryBoardStore, store: Store, manager: RunManager) -> None:
        """Two worker runs, one reviewer run, one compaction of the same
        session, and the event rows that link them to task T001."""
        base = datetime(2026, 1, 1, 10, 0)
        _add_run(store, "run_w1", "s1", "worker", base,
                 base + timedelta(minutes=10), 2.0, provider="local")
        _add_run(store, "run_w2", "s1", "worker",
                 base + timedelta(minutes=20), base + timedelta(minutes=25),
                 1.0, provider="local")
        _add_run(store, "run_r1", "s2", "reviewer",
                 base + timedelta(minutes=30), base + timedelta(minutes=35), 0.5)
        _add_run(store, "run_c1", "s1", "compact", base,
                 base + timedelta(minutes=1), 0.0, kind="compact")
        _write_run_events(manager.events, "run_c1", [
            {"kind": "compact", "session_id": "s1"},
        ])
        _write_run_events(manager.events, "run_w1", [
            {"kind": "thinking_state", "t": 100, "thinking": "on"},
            {"kind": "step", "step": 1, "started": 110, "input_tokens": 100_000,
             "output_tokens": 10_000, "cache_read_tokens": 0,
             "cache_write_tokens": 0, "generation_ms": 1000},
            {"kind": "thinking_switch", "t": 200, "thinking": "off"},
            {"kind": "step", "step": 2, "started": 210, "input_tokens": 100_000,
             "output_tokens": 10_000, "cache_read_tokens": 0,
             "cache_write_tokens": 0, "generation_ms": 1000},
        ])
        _write_run_events(manager.events, "run_w2", [
            {"kind": "step", "step": 1, "started": 100, "input_tokens": 100_000,
             "output_tokens": 10_000, "cache_read_tokens": 20_000,
             "cache_write_tokens": 0, "generation_ms": 1000},
            {"kind": "step", "step": 2, "started": 200, "input_tokens": 100_000,
             "output_tokens": 10_000, "cache_read_tokens": 30_000,
             "cache_write_tokens": 0, "generation_ms": 1000},
        ])
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="worker_completed", worker_run_id="run_w1",
            worker_status="completed",
        ))
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="review_parsed", worker_run_id="run_w1",
            review_status="needs_changes", review_score=55.0,
            review_reason="not enough", reviewer_run_id="run_r1",
            reviewer_status="completed",
        ))
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=1,
            event_type="worker_completed", worker_run_id="run_w2",
            worker_status="completed",
        ))
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=1,
            event_type="review_parsed", worker_run_id="run_w2",
            review_status="pass", review_score=90.0, review_reason="ok",
            reviewer_run_id="run_r1", reviewer_status="completed",
        ))

    def test_aggregates_of_synthetic_runs(self, board: InMemoryBoardStore,
                                          run_manager: RunManager):
        """Time, tokens, cache, cost, speed and thinking of the linked runs."""
        self._seed_board(board)
        store = run_manager.store
        self._link(board, store, run_manager)
        stats = mission_card_stats(run_manager, board, "sk1")

        # time: worker 10 + 5 min = 0.25 h; reviewer 5 min
        assert stats["runs"] == 4  # the linked runs plus the session's compaction
        assert stats["worker_hours"] == pytest.approx(0.25, abs=0.01)
        assert stats["reviewer_minutes"] == 5
        assert stats["compactions"] == 1
        # the compaction of the worker session is counted too (time and money)
        store.update_run("run_c1", cost_usd=0.05)
        stats = mission_card_stats(run_manager, board, "sk1")
        assert stats["runs"] == 4
        assert stats["cost_estimate_usd"] == pytest.approx(3.55)

        # tokens and cache
        assert stats["input_tokens"] == 400_000
        assert stats["output_tokens"] == 40_000
        assert stats["cache_read_tokens"] == 50_000
        assert stats["cache_ratio"] == pytest.approx(50_000 / 450_000, abs=0.001)

        # speed: 40k output tokens in 4 s
        assert stats["tok_per_s"] == pytest.approx(10_000)

        # thinking: run_w1's first step started while on was in force, the
        # second after the switch; run_w2 has no mode entries and defaults to on
        # (the sums count input + output + cache_read + cache_write per step)
        assert stats["thinking_runs"] == 3
        assert stats["thinking_off_runs"] == 1
        assert stats["thinking_steps"] == 4
        # on: 3 steps x (100k + 10k + 20k + 30k); off: 1 step x (100k + 10k)
        assert stats["thinking_tokens"] == 380_000
        assert stats["thinking_off_tokens"] == 110_000

        # cost: for a local run the runner's own price stands in for the stored
        # one (the local model costs nothing per token; the run is priced as
        # its cloud equivalent), the non-local runs keep the provider-reported
        # cost.  run_w1: off step 0.1M in @1 + 0.01M out @5 = 0.15,
        # on step 0.1M in @2 + 0.01M out @10 = 0.30;  run_w2 has no mode
        # entries, both steps default to on:
        # 0.1M in @2 + 0.01M out @10 + 0.02M cache_read @0.2 = 0.304,
        # 0.1M in @2 + 0.01M out @10 + 0.03M cache_read @0.2 = 0.306;
        # run_r1 (non-local): 0.5; run_c1 (no provider set): 0.05
        assert stats["cost_usd"] == pytest.approx(0.45 + 0.61 + 0.5 + 0.05, abs=0.01)
        # cost_real: only the non-local runs' provider-reported cost
        assert stats["cost_real_usd"] == pytest.approx(0.55)
        # local_cost_usd: the runner's own price of the whole mission (local
        # runs priced as their cloud equivalent, non-local at the reported cost)
        assert stats["local_cost_usd"] == pytest.approx(1.61, abs=0.01)

        # task T001: the last review is the pass of the second attempt
        assert stats["tasks"]["T001"] == {
            "attempts": 2, "last_review_score": 90.0,
            "last_review_reason": "ok",
        }
        assert stats["max_attempts_per_task"] == 2

    def test_a_run_in_neither_the_live_row_nor_the_events_is_not_counted(
        self, board: InMemoryBoardStore, run_manager: RunManager
    ):
        self._seed_board(board)
        store = run_manager.store
        self._link(board, store, run_manager)
        base = datetime(2026, 1, 1, 10, 0)
        _add_run(store, "run_x", "s9", "worker", base,
                 base + timedelta(minutes=1), 1.0)
        stats = mission_card_stats(run_manager, board, "sk1")
        assert stats["runs"] == 4
        assert stats["cost_estimate_usd"] == pytest.approx(3.5)

    def test_task_stats_with_per_attempt_time(self, board: InMemoryBoardStore,
                                              run_manager: RunManager):
        self._seed_board(board)
        store = run_manager.store
        self._link(board, store, run_manager)
        stats = task_stats(run_manager, board, "sk1", "T001")

        # the attempt's runs: its worker run plus the reviewer run (shared
        # with the other attempt, counted once, on the first attempt); the
        # session's compaction is counted in the aggregates too, like the
        # mission card does
        assert stats["runs"] == 4
        assert stats["attempts"] == 2
        # attempt 0: worker 10 min + reviewer 5 min; attempt 1: worker 5 min
        assert stats["attempt_seconds"] == [
            pytest.approx(900.0), pytest.approx(300.0),
        ]
        assert stats["attempts_detail"][0]["runs"] == ["run_r1", "run_w1"]
        assert stats["attempts_detail"][1]["runs"] == ["run_r1", "run_w2"]
        assert stats["last_review_score"] == 90.0
        assert stats["last_review_reason"] == "ok"

        # a task without any event row has empty figures and no attempts
        empty = task_stats(run_manager, board, "sk1", "T002")
        assert empty["runs"] == 0
        assert empty["attempts"] == 0
        assert empty["attempt_seconds"] == []
        assert empty["cost_estimate_usd"] == 0.0
        assert empty["last_review_score"] is None


class TestRunLinkage:
    """The mission's runs are found through the event log's ``worker_run_id``
    / ``reviewer_run_id`` rows (ids that are not ``run_``-shaped are kept out)."""

    def _board_with_runs(self, board: InMemoryBoardStore, manager: RunManager) -> None:
        _seed_mission(board, "m1", "sk1")
        store = manager.store
        base = datetime(2026, 1, 1, 10, 0)
        _add_run(store, "run_w1", "s1", "worker", base,
                 base + timedelta(minutes=1), 1.0)
        _add_run(store, "run_bad", "s1", "worker", base,
                 base + timedelta(minutes=1), 1.0)
        # a compaction of the session: the aggregated figures count it (as the
        # mission card does), while the per-attempt run lists stay explicit
        _add_run(store, "run_c1", "s1", "compact", base,
                 base + timedelta(minutes=1), 0.0, kind="compact")

    def test_links_runs_to_the_task_through_the_events(
        self, board: InMemoryBoardStore, run_manager: RunManager
    ):
        self._board_with_runs(board, run_manager)
        # run_w1 and run_bad both start with ``run_`` and are in the event rows
        # (so they both count); the session's compaction is counted in the
        # aggregates too (like in the mission card), while ``not_a_run_id`` is
        # kept out of both the run list and the attempt's run list
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="worker_completed", worker_run_id="run_w1",
            worker_status="completed",
        ))
        board.append_event("sk1", _event(
            "sk1", task_id="T001", task_index=0, task_attempt=0,
            event_type="review_parsed", worker_run_id="run_bad",
            review_status="pass", review_score=90.0, review_reason="ok",
            reviewer_run_id="not_a_run_id", reviewer_status="completed",
        ))
        stats = task_stats(run_manager, board, "sk1", "T001")
        assert stats["runs"] == 3
        assert stats["attempts_detail"][0]["runs"] == ["run_bad", "run_w1"]
        # the non-run id is neither counted as a run nor as an attempt
        assert stats["attempts"] == 1

    def test_live_state_row_also_carries_the_run_ids(
        self, board: InMemoryBoardStore, run_manager: RunManager
    ):
        self._board_with_runs(board, run_manager)
        # the run id is on the Live State row only: it counts as the mission's
        # run even without event rows
        board.upsert_live_state("sk1", None, plan={"tasks": [{"id": "T001"}]},
                               status="active", worker_run_id="run_w1",
                               worker_status="completed")
        stats = mission_card_stats(run_manager, board, "sk1")
        # the session's compaction counts in the aggregates too (as the
        # mission card does), so the mission has two runs, not one
        assert stats["runs"] == 2


class TestReadinessItems:
    """Each readiness check alone: ok, warning and error."""

    def test_working_folder_ok_warning_error(self):
        assert check_working_folder("/tmp")["status"] == "ok"
        assert check_working_folder(None)["status"] == "warning"
        item = check_working_folder("/no/such/dir")
        assert item["status"] == "error"
        assert item["check"] == "working_folder"
        # a host-only path is a warning, not an error
        assert check_working_folder("/x", host_visible=False)["status"] == "warning"

    def test_required_files_ok_warning_error(self):
        assert check_required_files(None)["status"] == "ok"
        assert check_required_files(["/tmp"], host_visible=False)["status"] == "warning"
        item = check_required_files(["/no/such/a.md"])
        assert item["status"] == "error"
        assert "/no/such/a.md" in item["reason"]

    def test_plan_check_ok_and_error(self):
        assert check_plan_present(LiveState(state_key="sk",
                                            plan={"tasks": [{"id": "T1"}]}))["status"] == "ok"
        assert check_plan_present(None)["status"] == "error"
        assert check_plan_present(
            LiveState(state_key="sk", plan={"tasks": []})
        )["status"] == "error"

    def test_live_state_check_ok_and_error(self):
        assert check_live_state_present(LiveState(state_key="sk",
                                                  status="active"))["status"] == "ok"
        assert check_live_state_present(None)["status"] == "error"

    def test_not_running_ok_and_error(self, board: InMemoryBoardStore):
        board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1")
        board.upsert_live_state("sk1", None, status="queued")
        assert check_not_running(board, "m1")["status"] == "ok"
        board.upsert_live_state("sk1", board.get_live_state("sk1").updated_at,
                               status="active")
        assert check_not_running(board, "m1")["status"] == "error"

    def test_preflight_error_warning_ok(self, board: InMemoryBoardStore):
        board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1")
        assert check_preflight_fresh(board, "m1")["status"] == "error"
        board.record_preflight("m1", [{"check": "ok"}])
        fresh = check_preflight_fresh(board, "m1")
        assert fresh["status"] == "ok"
        assert fresh["checked_at"]
        stale = check_preflight_fresh(board, "m1", max_age_seconds=-1)
        assert stale["status"] == "warning"
        assert "s old" in stale["reason"]

    def test_collect_readiness_gives_ok_warning_and_error_items(
        self, board: InMemoryBoardStore, tmp_path
    ):
        # warning: no working folder at all (checked on the host); the mission's
        # errors come from the missing Live State row and plan
        board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1")
        summary = collect_readiness_sync(board, board.get_mission("m1"))
        folder = next(i for i in summary["items"] if i["check"] == "working_folder")
        assert folder["status"] == "warning"
        assert summary["status"] == "error"
        assert start_guard(summary)["allowed"] is False

        # warning + ok: a real folder and files, no URL to check against
        workdir = tmp_path / "mission"
        workdir.mkdir()
        (workdir / "a.md").write_text("x")
        board._missions["m1"].working_directory = str(workdir)
        board.set_mission_config("m1", {"required_files": [str(workdir / "a.md")]},
                                None)
        board.upsert_live_state("sk1", None, plan={"tasks": [{"id": "T1"}]},
                               status="queued")
        board.record_preflight("m1", [{"check": "ok"}])
        summary = collect_readiness_sync(board, board.get_mission("m1"))
        by_check = {i["check"]: i["status"] for i in summary["items"]}
        assert by_check["working_folder"] == "ok"
        assert by_check["required_files"] == "ok"
        assert by_check["plan"] == "ok"
        assert by_check["live_state"] == "ok"
        assert by_check["not_running"] == "ok"
        assert by_check["preflight"] == "ok"
        # without settings the URL checks are warnings
        assert by_check["runner"] == "warning"
        assert by_check["litellm"] == "warning"
        assert by_check["llama_model"] == "warning"
        assert summary["status"] == "warning"
        # no errors: the start is allowed
        assert start_guard(summary)["allowed"] is True
        assert start_guard(summary, confirmed=True)["allowed"] is True


def collect_readiness_sync(board: InMemoryBoardStore, mission: Mission) -> dict:
    """collect_readiness without the async event loop (the URL checks are
    skipped: settings=None gives them their warning items)."""
    import asyncio

    return asyncio.run(collect_readiness(board, mission, settings=None))


class TestStartGuardBlocked:
    """The start is blocked on error checks unless confirmed."""

    def test_error_blocks_the_start_until_confirmed(self, board: InMemoryBoardStore):
        # no live state: the live_state and plan checks are errors
        board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1")
        summary = collect_readiness_sync(board, board.get_mission("m1"))
        assert any(i["status"] == "error" for i in summary["items"])
        guard = start_guard(summary)
        assert guard["allowed"] is False
        assert guard["blocked_by"]
        assert any(i["check"] == "live_state" for i in guard["blocked_by"])
        assert guard["confirmed"] is False
        # warnings alone never block
        assert all(i["status"] in ("ok", "warning")
                   for i in summary["items"] if i not in summary["errors"])
        # confirming lets the start through
        assert start_guard(summary, confirmed=True)["allowed"] is True

    def test_running_mission_blocks_the_start(self, board: InMemoryBoardStore):
        _seed_mission(board, "m1", "sk1")
        board.upsert_live_state("sk1", board.get_live_state("sk1").updated_at,
                               status="active")
        summary = collect_readiness_sync(board, board.get_mission("m1"))
        guard = start_guard(summary)
        assert guard["allowed"] is False
        assert any(i["check"] == "not_running" for i in guard["blocked_by"])
        # even with confirm the start_guard only overrides errors; running is
        # an error, so the confirmed start is allowed by the guard itself
        assert start_guard(summary, confirmed=True)["allowed"] is True


class TestRollbackAndBackupRow:
    """The rollback restores the entry's before_json, and the backup row is
    saved to the changelog before every change."""

    def test_backup_row_is_saved_before_the_change(self, board: InMemoryBoardStore):
        board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1",
                                        objective="original")
        before = board.get_mission("m1").model_dump()
        board.update_mission("m1", None, objective="changed")
        entry = next(e for e in board.changelog() if e.action == "mission update")
        # the backup row is the full previous row, stored before the change
        assert entry.before_json == before
        assert entry.before_json["objective"] == "original"
        assert entry.after_json["objective"] == "changed"
        # the live-state write audits its backup row too
        board.upsert_live_state("sk1", None, status="active")
        live_entry = next(
            e for e in board.changelog() if e.action == "live state upsert"
        )
        assert live_entry.before_json["state_key"] == "sk1"
        # a never-set row keeps only its key: no fields were there to restore
        assert "status" not in live_entry.before_json
        assert live_entry.after_json["status"] == "active"

    def test_rollback_restores_the_backup_row(self, board: InMemoryBoardStore):
        board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1",
                                        objective="original")
        version = board.get_mission("m1").updated_at
        board.update_mission("m1", version, objective="changed")
        entry_id = next(e.id for e in board.changelog()
                        if e.action == "mission update" and e.mission_id == "m1")
        result = rollback(board, entry_id)
        assert result["restored"]["objective"] == "original"
        assert board.get_mission("m1").objective == "original"
        # the rollback itself is a change: the changelog gets a "rollback"
        # row and its backup row is the pre-rollback state
        rollback_entry = board.changelog()[0]
        assert rollback_entry.action == "rollback"
        assert rollback_entry.before_json["objective"] == "changed"
        assert rollback_entry.after_json["objective"] == "original"

    def test_rollback_conflicts_when_the_row_changed_after_the_entry(
        self, board: InMemoryBoardStore
    ):
        board._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1",
                                        objective="original")
        version = board.get_mission("m1").updated_at
        board.update_mission("m1", version, objective="changed")
        entry_id = next(e.id for e in board.changelog()
                        if e.action == "mission update" and e.mission_id == "m1")
        # a second change after the entry: the entry's after_json no longer
        # matches the row, so the rollback is refused
        version2 = board.get_mission("m1").updated_at
        board.update_mission("m1", version2, objective="changed again")
        with pytest.raises(ConflictError):
            rollback(board, entry_id)
        # the row keeps its newer value
        assert board.get_mission("m1").objective == "changed again"

    def test_rollback_of_a_live_state_row_restores_status_and_plan(
        self, board: InMemoryBoardStore
    ):
        board.upsert_live_state("sk1", None, plan={"tasks": [{"id": "T1"}]},
                               status="queued", worker_run_id="run_a")
        version = board.get_live_state("sk1").updated_at
        board.upsert_live_state("sk1", version, status="active",
                               worker_run_id="run_b")
        entry_id = next(
            e.id for e in board.changelog() if e.action == "live state upsert"
        )
        result = rollback(board, entry_id)
        assert result["restored"]["status"] == "queued"
        assert result["restored"]["worker_run_id"] == "run_a"
        live = board.get_live_state("sk1")
        assert live.status == "queued"
        assert live.worker_run_id == "run_a"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "--no-cov", "-q"]))
