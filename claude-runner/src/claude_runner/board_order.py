"""Python twin of board_order.js: ordering rules for the waiting tasks of a mission.

Keep the two files in sync.
"""

import re

_TASK_ID = re.compile(r"T[0-9]+[A-Z]*")


def parse_task_id(text):
    s = str(text)
    return s if _TASK_ID.fullmatch(s) else None


def dependents(deps, id_):
    return [k for k in deps if id_ in deps[k]]


def closure(deps, id_, direction):
    down = direction == "down"
    seen = set()
    out = []
    stack = [id_]
    while stack:
        x = stack.pop()
        nxt = dependents(deps, x) if down else deps.get(x, [])
        for n in nxt:
            if n != id_ and n not in seen and n in deps:
                seen.add(n)
                out.append(n)
                stack.append(n)
    return out


def violations(order, deps):
    pos = {id_: i for i, id_ in enumerate(order)}
    out = []
    for id_ in order:
        for d in deps.get(id_, []):
            if d in pos and pos[d] > pos[id_]:
                out.append([d, id_])
    return out


def would_cycle(deps, frm, to):
    if frm == to or to in closure(deps, frm, "down"):
        return [to, frm]
    return None


def move_order(order, id_, to_index):
    ids = set(id_) if isinstance(id_, (list, tuple)) else {id_}
    block = [x for x in order if x in ids]
    rest = [x for x in order if x not in ids]
    at = min(max(0, to_index), len(rest))
    return rest[:at] + block + rest[at:]


def repair_order(order, deps):
    left = list(order)
    in_order = set(order)
    placed = set()
    out = []
    while left:
        i = next(
            (
                j
                for j, id_ in enumerate(left)
                if all(d not in in_order or d in placed for d in deps.get(id_, []))
            ),
            -1,
        )
        if i < 0:
            raise ValueError("dependency cycle")
        placed.add(left[i])
        out.append(left.pop(i))
    return out


def try_move(tasks, id_, to_index):
    by_id = {}
    w_ids = []
    for t in tasks:
        by_id[t["id"]] = t
        if t["st"] == "g":
            w_ids.append(t["id"])
    waiting = set(w_ids)
    deps = {w: [d for d in by_id[w]["deps"] if d in waiting] for w in w_ids}

    def full(wait_order):
        it = iter(wait_order)
        return [next(it) if t["st"] == "g" else t["id"] for t in tasks]

    if id_ not in by_id:
        return {"ok": False, "reason": "unknown", "broken": [], "group": None}
    if id_ not in waiting:
        return {"ok": False, "reason": "locked", "broken": [], "group": None}

    frm = w_ids.index(id_)
    to = min(max(0, to_index), len(w_ids) - 1)
    if to == frm:
        return {"ok": True, "order": full(w_ids)}

    plain = move_order(w_ids, id_, to)
    broken = violations(plain, deps)
    if not broken:
        return {"ok": True, "order": full(plain)}

    grp = [id_] + closure(deps, id_, "down" if to > frm else "up")
    group_order = move_order(w_ids, grp, to)
    group_broken = violations(group_order, deps)
    if group_broken:
        return {"ok": False, "reason": "group-invalid", "broken": group_broken, "group": None}
    return {
        "ok": False,
        "reason": "dependency",
        "broken": broken,
        "group": {"ids": grp, "order": full(group_order)},
    }
