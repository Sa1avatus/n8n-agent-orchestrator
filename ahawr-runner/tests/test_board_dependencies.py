"""Tests for the pure mission-dependency rule in ``board_dependencies``.

The module touches no storage, so these tests build the queue and the state
mapping by hand.
"""

from __future__ import annotations

import pytest

import ahawr_runner.board_dependencies as BD


def test_build_dependencies_keeps_order_and_drops_blanks_and_duplicates() -> None:
    assert BD.build_dependencies(None) == []
    assert BD.build_dependencies([]) == []
    assert BD.build_dependencies(["m2", "m1", "m2", "  ", "m1"]) == ["m2", "m1"]
    assert BD.build_dependencies(("m1", "m1")) == ["m1"]
    assert BD.build_dependencies(["m1", "m1"]) == ["m1"]
    # the self-reference is kept: validate_dependencies names it
    assert BD.build_dependencies(["m1", "m1", "m2"]) == ["m1", "m2"]


def test_build_dependencies_rejects_shapes_the_api_cannot_store() -> None:
    with pytest.raises(ValueError, match="not a string"):
        BD.build_dependencies("m1")
    with pytest.raises(ValueError, match="must be a list of mission ids"):
        BD.build_dependencies({"m1": "m2"})
    with pytest.raises(ValueError, match="must be a mission id string"):
        BD.build_dependencies(["m1", 3])
    with pytest.raises(ValueError, match="must be a mission id string"):
        BD.build_dependencies([None])


def test_find_unknown_dependencies_lists_ids_missing_from_queue_and_completed() -> None:
    assert BD.find_unknown_dependencies(["m1", "m2"], ["m1"], []) == ["m2"]
    assert BD.find_unknown_dependencies(["m1", "m2"], ["m1"], ["m2"]) == []
    assert BD.find_unknown_dependencies([], ["m1"], []) == []


def test_find_cycle_returns_the_path_and_none_for_a_tree() -> None:
    assert BD.find_cycle({"a": ["b"], "b": ["a"]}) == ["a", "b", "a"]
    assert BD.find_cycle({"a": ["a"]}) == ["a", "a"]
    assert BD.find_cycle({"a": ["b"], "b": ["c"], "c": ["a", "b"]}) == ["a", "b", "c", "a"]
    assert BD.find_cycle({"a": ["b"], "b": [], "c": ["a"]}) is None
    assert BD.find_cycle({}) is None


def test_find_cycle_terminates_on_a_long_chain_without_recursion() -> None:
    chain = {f"m{i}": [f"m{i + 1}"] for i in range(400)}
    chain["m400"] = []
    assert BD.find_cycle(chain) is None


def test_validate_dependencies_names_the_self_dependency_the_unknown_id_and_the_cycle() -> None:
    queue_ids = ["m1", "m2", "m3"]
    completed_ids = ["m4"]
    graph = {"m1": [], "m2": ["m1"], "m3": ["m2"]}

    assert BD.validate_dependencies("m1", [], queue_ids, completed_ids, graph) == []
    assert BD.validate_dependencies("m1", ["m4"], queue_ids, completed_ids, graph) == []
    assert BD.validate_dependencies(
        "m1", ["m1"], queue_ids, completed_ids, graph
    ) == ["mission m1 cannot depend on itself"]
    assert BD.validate_dependencies(
        "m1", ["ghost"], queue_ids, completed_ids, graph
    ) == [
        "unknown mission dependency ghost: it is neither in the queue nor completed"
    ]
    assert BD.validate_dependencies(
        "m2", ["m2"], queue_ids, completed_ids, graph
    ) == ["mission m2 cannot depend on itself"]
    assert BD.validate_dependencies(
        "m1", ["m2"], queue_ids, completed_ids, graph
    ) == ["the mission dependency closes a cycle: m1 -> m2 -> m1"]


def test_classify_queue_marks_empty_dependencies_as_due() -> None:
    entries = [{"position": 0, "mission_id": "m1", "depends_on": []}]
    assert BD.classify_queue(entries, {}) == {
        "m1": {"state": "due", "reason": "", "depends_on": []}
    }


def test_classify_queue_reports_due_blocked_and_waiting_with_reasons() -> None:
    entries = [
        {"position": 0, "mission_id": "m1", "depends_on": ["m2", "m3"]},
        {"position": 1, "mission_id": "m2", "depends_on": ["m3"]},
        {"position": 2, "mission_id": "m3", "depends_on": ["m4"]},
    ]
    states = {"m2": "completed", "m3": "failed", "m4": "queued"}
    verdicts = BD.classify_queue(entries, states)
    assert verdicts["m1"]["state"] == "blocked"
    assert verdicts["m1"]["reason"] == "blocked by m3: a dependency failed"
    assert verdicts["m1"]["depends_on"] == ["m2", "m3"]
    assert verdicts["m2"]["state"] == "blocked"
    assert verdicts["m3"]["state"] == "waiting"
    assert verdicts["m3"]["reason"] == "waiting for m4"
    # a dependency with no state row is unmet, so the mission waits for it
    assert BD.classify_queue(
        [{"position": 0, "mission_id": "m1", "depends_on": ["m2"]}], {}
    )["m1"] == {"state": "waiting", "reason": "waiting for m2", "depends_on": ["m2"]}


def test_find_first_due_resumes_the_active_mission_before_a_due_one() -> None:
    entries = [
        {"position": 0, "mission_id": "m1", "depends_on": []},
        {"position": 1, "mission_id": "m2", "depends_on": []},
    ]
    states = {"m1": "queued", "m2": "active"}
    first_due = BD.find_first_due(entries, states)
    assert first_due is not None
    assert first_due["mission_id"] == "m2"


def test_find_first_due_returns_the_first_due_entry_in_position_order() -> None:
    entries = [
        {"position": 0, "mission_id": "m1", "depends_on": ["m3"]},
        {"position": 1, "mission_id": "m2", "depends_on": []},
        {"position": 2, "mission_id": "m3", "depends_on": []},
    ]
    states = {"m1": "queued", "m2": "queued", "m3": "queued"}
    # m1 waits for m3, so the first due entry at position 1 is m2
    first_due = BD.find_first_due(entries, states)
    assert first_due is not None
    assert first_due["mission_id"] == "m2"

    states["m3"] = "failed"
    states["m2"] = "completed"
    # m2 is completed, m1 is blocked by m3, m3 is due
    first_due = BD.find_first_due(entries, states)
    assert first_due is not None
    assert first_due["mission_id"] == "m3"


def test_find_first_due_skips_a_completed_mission_and_blocked_missions() -> None:
    entries = [
        {"position": 0, "mission_id": "m1", "depends_on": []},
        {"position": 1, "mission_id": "m2", "depends_on": ["m4"]},
        {"position": 2, "mission_id": "m3", "depends_on": ["m2"]},
    ]
    states = {"m1": "completed", "m2": "failed", "m3": "queued", "m4": "queued"}
    # m1 is already completed, m2 waits on m4, m3 is blocked by m2
    assert BD.find_first_due(entries, states) is None
    states["m4"] = "completed"
    first_due = BD.find_first_due(entries, states)
    assert first_due is not None
    assert first_due["mission_id"] == "m2"

    states["m2"] = "active"
    resumed = BD.find_first_due(entries, states)
    assert resumed is not None
    assert resumed["mission_id"] == "m2"


# ---------------------------------------------------------------------------
# the Architect dependency block (docs/mission-dependencies.md, point (g))
# ---------------------------------------------------------------------------


def test_build_dependency_context_architect_dependencies_names_id_name_state_and_report() -> None:
    mission = {"mission_id": "m3", "depends_on": ["m1", "m2"]}
    rows = [
        {
            "mission_id": "m1",
            "name": "Source",
            "report": "the report of m1",
            "summary": "the summary of m1",
        },
        {"mission_id": "m2", "name": "Helper", "report": "", "summary": "the summary of m2"},
    ]
    states = {"m1": "completed", "m2": "active"}
    block = BD.build_dependency_context(mission, rows, states)

    assert block.startswith(BD.DEPENDENCY_BLOCK_HEADING)
    assert 'm1 "Source" [completed]: the report of m1' in block
    # a dependency with no report falls back to its plan summary
    assert 'm2 "Helper" [active]: the summary of m2' in block
    assert "Rule: " + BD.ARCHITECT_DEPENDENCY_RULE in block
    assert BD.ARCHITECT_DEPENDENCY_RULE in block


def test_build_dependency_context_architect_dependencies_falls_back_for_unknown_rows() -> None:
    block = BD.build_dependency_context({"depends_on": ["zz"], "mission_id": "m3"}, [], {})
    assert 'zz "zz" [unknown]' in block
    assert BD.DEPENDENCY_TEXT_FALLBACK in block


def test_build_dependency_context_architect_dependencies_cuts_to_the_constants() -> None:
    long_report = "word " * 400  # 2000 characters, over DEPENDENCY_REPORT_MAX_CHARS
    block = BD.build_dependency_context(
        {"depends_on": ["m1"]},
        [{"mission_id": "m1", "name": "Source", "report": long_report, "summary": ""}],
        {"m1": "completed"},
    )
    assert len(block) <= BD.ARCHITECT_BLOCK_MAX_CHARS
    carried = block.splitlines()[1].split("]: ", 1)[1]
    assert len(carried) == BD.DEPENDENCY_REPORT_MAX_CHARS
    assert carried.endswith(BD.TRUNCATION_MARK)
    assert long_report[: BD.DEPENDENCY_REPORT_MAX_CHARS - len(BD.TRUNCATION_MARK)] in block

    many = {"depends_on": [f"m{i}" for i in range(40)]}
    rows = [
        {"mission_id": f"m{i}", "name": f"Mission {i}", "report": "x" * 200, "summary": ""}
        for i in range(40)
    ]
    states = {f"m{i}": "completed" for i in range(40)}
    big = BD.build_dependency_context(many, rows, states)
    assert len(big) <= BD.ARCHITECT_BLOCK_MAX_CHARS
    assert big.endswith(BD.TRUNCATION_MARK)


def test_find_part_order_problems_accepts_parts_using_only_earlier_parts_or_own_tasks() -> None:
    parts = [["t1", "t2"], ["t3", "t4"]]
    tasks_by_part = {
        1: [
            {"id": "t1", "dependencies": [], "scope": ["app/main.py"], "produced": ["app/main.py"]},
            {"id": "t2", "dependencies": ["t1"], "scope": ["app/main.py"], "produced": []},
        ],
        2: [
            {
                "id": "t3",
                "dependencies": ["t2"],
                "scope": ["app/main.py", "tests/test_main.py"],
                "produced": ["tests/test_main.py"],
            },
            {"id": "t4", "dependencies": ["t3"], "scope": ["README.md"]},
        ],
    }
    assert BD.find_part_order_problems(parts, tasks_by_part) == []
    # an item produced nowhere at all is no problem: the lint proves nothing about it
    nowhere = BD.find_part_order_problems([["t1"], ["t2"]], {1: [{"id": "t1", "scope": ["x.py"]}]})
    assert nowhere == []


def test_find_part_order_problems_names_the_task_the_item_and_the_later_part() -> None:
    parts = [["t1"], ["t2"]]
    tasks_by_part = {
        1: [{"id": "t1", "dependencies": [], "scope": ["src/later.py"]}],
        2: [{"id": "t2", "scope": ["src/later.py"], "produced": ["src/later.py"]}],
    }
    problems = BD.find_part_order_problems(parts, tasks_by_part)
    assert len(problems) == 1
    assert "task t1" in problems[0]
    assert "part 1" in problems[0]
    assert "src/later.py" in problems[0]
    assert "part 2" in problems[0]
    # a task id in a later part is also an item produced only there
    id_problems = BD.find_part_order_problems(
        parts, {1: [{"id": "t1", "dependencies": ["t2"]}], 2: [{"id": "t2", "dependencies": []}]}
    )
    assert id_problems == ["task t1 of part 1 names t2, which is produced only in part 2"]


def test_find_part_order_problems_accepts_same_part_and_produces_once_per_item() -> None:
    parts = [["t1", "t2"]]
    tasks_by_part = {1: [{"id": "t1", "scope": ["src/new.py"], "produced": []},
                        {"id": "t2", "scope": ["src/new.py"], "produced": ["src/new.py"]}]}
    assert BD.find_part_order_problems(parts, tasks_by_part) == []
    # the same scope file named twice by one task gets one message
    parts = [["t1"], ["t2"]]
    tasks_by_part = {
        1: [{"id": "t1", "dependencies": ["src/later.py"], "scope": ["src/later.py"]}],
        2: [{"id": "t2", "produced": ["src/later.py"]}],
    }
    problems = BD.find_part_order_problems(parts, tasks_by_part)
    assert len(problems) == 1


def test_find_part_order_problems_reports_every_item_of_a_task_that_lives_in_a_later_part() -> None:
    parts = [["t1"], ["t2"], ["t3"]]
    tasks_by_part = {
        1: [{"id": "t1", "dependencies": ["t3"], "scope": ["tests/test_x.py"]}],
        2: [{"id": "t2", "dependencies": ["t1"], "produced": ["src/x.py"]}],
        3: [{"id": "t3", "scope": ["src/x.py"], "produced": ["tests/test_x.py"]}],
    }
    problems = BD.find_part_order_problems(parts, tasks_by_part)
    assert len(problems) == 2
    assert problems[0].startswith("task t1 of part 1 names t3")
    assert problems[1].startswith("task t1 of part 1 names tests/test_x.py")


def test_build_dependency_context_architect_dependencies_is_empty_without_dependencies() -> None:
    assert BD.build_dependency_context({"mission_id": "m1", "depends_on": None}, [], {}) == ""
    assert BD.build_dependency_context({"mission_id": "m1", "depends_on": []}, [], {}) == ""
    assert BD.build_dependency_context({"depends_on": ["  ", ""]}, [{"mission_id": "m1"}], {}) == ""
