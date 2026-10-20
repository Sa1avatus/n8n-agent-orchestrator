"""T007 — letter-suffixed task ids (T013A, T013B): every runner parser
accepts ``T[0-9]+[A-Z]*`` and the runner's sort key orders a suffixed id
right after its number, before the next one (T013, T013A, T013B, T014)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_runner import board_api, board_order, board_stats, runs, titles  # noqa: E402
from claude_runner.board import InMemoryBoardStore, Mission  # noqa: E402


def test_task_title_keeps_letter_suffix():
    assert titles.title_of("TASK T013A: write the parser\nbody").startswith("TASK T013A")


def test_task_number_accepts_letter_suffix():
    # T013A is task 13: the letter is part of the id, not a separate task number
    assert runs.task_number("TASK T013A: x") == 13
    assert runs.task_number("TASK T013: x") == 13
    assert runs.task_number("no task here") == 0


def test_board_parsers_accept_letter_suffix():
    assert board_order.parse_task_id("T013A") == "T013A"
    assert board_order.parse_task_id("T013B") == "T013B"
    assert board_order.parse_task_id("T013") == "T013"
    assert board_order.parse_task_id("T014") == "T014"
    assert board_order.parse_task_id("T01") == "T01"
    assert board_order.parse_task_id("t013") is None
    assert board_order.parse_task_id("T013a") is None  # lowercase is not a suffix
    assert board_api._TASK_ID_RE.match("T013A")
    assert board_api._TASK_ID_RE.match("T013B")
    assert not board_api._TASK_ID_RE.match("T013a")


def test_task_sort_key_orders_suffixes_between_number_and_next():
    ids = ["T014", "T013B", "T013", "T013A"]
    assert sorted(ids, key=board_order.task_sort_key) == ["T013", "T013A", "T013B", "T014"]
    # a suffix-less id sorts before its suffixed twins
    assert sorted(["T013B", "T013", "T013A"], key=board_order.task_sort_key) == [
        "T013", "T013A", "T013B"
    ]
    # T002 sorts before T002A, and both before T003
    assert sorted(["T003", "T002A", "T002"], key=board_order.task_sort_key) == [
        "T002", "T002A", "T003"
    ]


def test_board_stats_task_views_use_the_runner_sort_key():
    # board_stats uses the shared key (board_order.task_sort_key):
    # a suffixed id is keyed by (number, suffix), so the views come out in
    # T013, T013A, T013B, T014 order regardless of how they were stored.
    ordered = ["T014", "T013B", "T013", "T013A"]
    assert sorted(ordered, key=board_stats.task_sort_key) == [
        "T013", "T013A", "T013B", "T014"
    ]
    # the suffixed ids sort strictly after T013 and before T014
    assert board_stats.task_sort_key("T013") < board_stats.task_sort_key("T013A")
    assert board_stats.task_sort_key("T013A") < board_stats.task_sort_key("T014")


def test_mission_card_stats_keeps_letter_suffixed_task_ids():
    # board_stats.mission_card_stats reads its task ids from board.task_views
    # and sorts them with task_sort_key: T013A stays T013A (not T013) and
    # the tasks dict comes out in T013, T013A, T013B, T014 order.
    store = InMemoryBoardStore()
    store._missions["m1"] = Mission(mission_id="m1", state_namespace="sk1")
    store.upsert_live_state(
        "sk1",
        None,
        plan={
            "tasks": [
                {"id": "T014", "title": "four"},
                {"id": "T013B", "title": "thirteen B"},
                {"id": "T013", "title": "thirteen"},
                {"id": "T013A", "title": "thirteen A"},
            ]
        },
        status="active",
    )

    class FakeManager:
        local_pricing = type("Pricing", (), {"enabled": False})

        def get_run(self, run_id):
            return None

    card = board_stats.mission_card_stats(FakeManager(), store, "sk1")
    assert card["state_key"] == "sk1"
    assert list(card["tasks"].keys()) == ["T013", "T013A", "T013B", "T014"]
