import random
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_runner import board_order as BO  # noqa: E402


def t(id_, deps=None, st="g"):
    return {"id": id_, "deps": deps or [], "st": st}


def deps_of(tasks):
    waiting = {x["id"] for x in tasks if x["st"] == "g"}
    return {x["id"]: [d for d in x["deps"] if d in waiting] for x in tasks if x["st"] == "g"}


def waiting_order(tasks):
    return [x["id"] for x in tasks if x["st"] == "g"]


def reorder(tasks, order):
    by = {x["id"]: x for x in tasks}
    return [by[id_] for id_ in order]


def test_parse_task_id_accepts_letter_suffix_and_rejects_others():
    assert BO.parse_task_id("T001") == "T001"
    assert BO.parse_task_id("T013A") == "T013A"
    assert BO.parse_task_id("T013AB") == "T013AB"
    assert BO.parse_task_id("t001") is None
    assert BO.parse_task_id("T001 x") is None
    assert BO.parse_task_id("T001\n") is None
    assert BO.parse_task_id("T") is None


def test_chain_moving_root_down_pulls_dependents():
    tasks = [t("T1"), t("T2", ["T1"]), t("T3", ["T2"]), t("T4", ["T3"])]
    r = BO.try_move(tasks, "T1", 3)
    assert r["ok"] is False
    assert r["reason"] == "dependency"
    assert r["broken"] == [["T1", "T2"]]
    assert r["group"]["ids"] == ["T1", "T2", "T3", "T4"]
    assert r["group"]["order"] == ["T1", "T2", "T3", "T4"]


def test_chain_moving_last_to_front_is_refused_group_pulls_dependencies():
    tasks = [t("T1"), t("T2", ["T1"]), t("T3", ["T2"]), t("T4", ["T3"])]
    r = BO.try_move(tasks, "T4", 0)
    assert r["ok"] is False
    assert r["broken"] == [["T3", "T4"]]
    assert r["group"]["ids"] == ["T4", "T3", "T2", "T1"]
    assert r["group"]["order"] == ["T1", "T2", "T3", "T4"]


def test_diamond_moving_t4_to_front_offers_whole_closure():
    tasks = [t("T1"), t("T2", ["T1"]), t("T3", ["T1"]), t("T4", ["T2", "T3"])]
    r = BO.try_move(tasks, "T4", 0)
    assert r["ok"] is False
    assert sorted(r["group"]["ids"]) == ["T1", "T2", "T3", "T4"]
    assert r["group"]["order"] == ["T1", "T2", "T3", "T4"]


def test_two_roots_independent_move_applied_as_is():
    tasks = [t("T1"), t("T2"), t("T3", ["T1"]), t("T4", ["T2"])]
    assert BO.try_move(tasks, "T2", 0) == {"ok": True, "order": ["T2", "T1", "T3", "T4"]}


def test_moving_first_task_to_end_with_no_dependents():
    tasks = [t("T1"), t("T2"), t("T3")]
    assert BO.try_move(tasks, "T1", 2) == {"ok": True, "order": ["T2", "T3", "T1"]}


def test_moving_last_task_to_front_with_no_dependencies():
    tasks = [t("T1"), t("T2"), t("T3")]
    assert BO.try_move(tasks, "T3", 0) == {"ok": True, "order": ["T3", "T1", "T2"]}


def test_cycle_attempt_returns_closing_pair_or_none():
    deps = {"T1": [], "T2": ["T1"], "T3": ["T2"]}
    assert BO.would_cycle(deps, "T1", "T3") == ["T3", "T1"]
    assert BO.would_cycle(deps, "T1", "T1") == ["T1", "T1"]
    assert BO.would_cycle(deps, "T3", "T1") is None


def test_group_move_moving_up_pulls_the_dependency():
    tasks = [t("T1"), t("T2"), t("T3"), t("T4", ["T2"])]
    r = BO.try_move(tasks, "T4", 0)
    assert r["ok"] is False
    assert r["broken"] == [["T2", "T4"]]
    assert r["group"]["ids"] == ["T4", "T2"]
    assert r["group"]["order"] == ["T2", "T4", "T1", "T3"]


def test_refused_group_move_returns_broken_pairs_and_no_group():
    tasks = [t("T1"), t("T2", ["T1"]), t("T3"), t("T4"), t("T5", ["T1", "T4"])]
    r = BO.try_move(tasks, "T1", 1)
    assert r["ok"] is False
    assert r["reason"] == "group-invalid"
    assert r["group"] is None
    assert r["broken"] == [["T4", "T5"]]


def test_locked_tasks_never_change_position():
    tasks = [t("T1"), t("T2", [], "p"), t("T3"), t("T4", [], "a"), t("T5")]
    r = BO.try_move(tasks, "T1", 2)
    assert r == {"ok": True, "order": ["T3", "T2", "T5", "T4", "T1"]}


def test_rejected_task_is_locked_too():
    tasks = [t("T1"), t("T2", [], "f"), t("T3")]
    r = BO.try_move(tasks, "T1", 2)
    assert r == {"ok": True, "order": ["T3", "T2", "T1"]}


def test_locked_task_cannot_be_moved_and_unknown_id_is_refused():
    tasks = [t("T1"), t("T2", [], "f"), t("T3")]
    assert BO.try_move(tasks, "T2", 0) == {"ok": False, "reason": "locked", "broken": [], "group": None}
    assert BO.try_move(tasks, "T9", 0) == {"ok": False, "reason": "unknown", "broken": [], "group": None}


def test_violations_and_move_order_block_move_keeps_relative_order():
    deps = {"T1": [], "T2": ["T1"], "T3": []}
    assert BO.violations(["T2", "T1", "T3"], deps) == [["T1", "T2"]]
    assert BO.move_order(["T1", "T2", "T3", "T4"], ["T2", "T4"], 0) == ["T2", "T4", "T1", "T3"]


def test_repair_order_moves_only_what_is_needed():
    deps = {"T1": [], "T2": ["T1"], "T3": [], "T4": ["T3"]}
    assert BO.repair_order(["T2", "T1", "T4", "T3"], deps) == ["T1", "T2", "T3", "T4"]
    assert BO.repair_order(["T3", "T1", "T2"], deps) == ["T3", "T1", "T2"]
    with pytest.raises(ValueError, match="cycle"):
        BO.repair_order(["T1", "T2"], {"T1": ["T2"], "T2": ["T1"]})


def test_200_seeded_random_dags_and_moves():
    rng = random.Random(20260518)
    for _ in range(200):
        n = rng.randint(2, 10)
        names = [f"T{i + 1}" for i in range(n)]
        rank = names[:]
        rng.shuffle(rank)
        pos = {id_: i for i, id_ in enumerate(rank)}
        tasks = []
        for id_ in names:
            deps = [d for d in names if pos[d] < pos[id_] and rng.random() < 0.3]
            tasks.append(t(id_, deps, "p" if rng.random() < 0.2 else "g"))

        fixed = BO.repair_order(rng.sample(waiting_order(tasks), len(waiting_order(tasks))), deps_of(tasks))
        shuffled = rng.sample(tasks, len(tasks))
        by = {x["id"]: x for x in tasks}
        fi = iter(fixed)
        tasks = [by[next(fi)] if x["st"] == "g" else x for x in shuffled]

        for _ in range(5):
            before = [x["id"] for x in tasks]
            st = {x["id"]: x["st"] for x in tasks}
            id_ = names[rng.randrange(n)]
            to = rng.randrange(n)
            r = BO.try_move(tasks, id_, to)
            deps = deps_of(tasks)

            if r["ok"]:
                assert sorted(r["order"]) == sorted(names), "ids lost or duplicated"
                for i, x in enumerate(tasks):
                    if x["st"] != "g":
                        assert r["order"][i] == before[i], "locked task moved"
                w = [x for x in r["order"] if st[x] == "g"]
                assert BO.violations(w, deps) == [], "dependency violated after move"
                tasks = reorder(tasks, r["order"])
            else:
                assert isinstance(r["broken"], list)
                if r["group"]:
                    g = r["group"]["order"]
                    assert sorted(g) == sorted(names), "group lost or duplicated ids"
                    for i, x in enumerate(tasks):
                        if x["st"] != "g":
                            assert g[i] == before[i], "locked task moved by group"
                    gw = [x for x in g if st[x] == "g"]
                    assert BO.violations(gw, deps) == [], "group move violates a dependency"
                    if rng.random() < 0.5:
                        tasks = reorder(tasks, g)
