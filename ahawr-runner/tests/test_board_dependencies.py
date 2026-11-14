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
