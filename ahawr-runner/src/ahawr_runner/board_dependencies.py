"""Pure mission-dependency rules: normalize, validate, classify, pick.

Design: ``docs/mission-dependencies.md`` section "Design: mission dependencies".

No storage and no network here: every function takes the queue entries and the
Live State values the caller already read, so the board API and
``data-tables/ops/queue_board.py`` share one rule.  Neither ``board_pg.py`` nor
``board_api.py`` is imported, keeping the import graph acyclic.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, TypedDict

DependencyStateName = Literal["due", "waiting", "blocked"]

#: The heading of the Architect block listing one mission's dependencies.
DEPENDENCY_BLOCK_HEADING = "Missions this one depends on"
#: One dependency's report or plan summary inside the block (characters).
DEPENDENCY_REPORT_MAX_CHARS = 700
#: The whole Architect dependency block (characters), heading, lines and rule.
ARCHITECT_BLOCK_MAX_CHARS = 4000
#: The sentence the Architect must follow after the dependency lines.
ARCHITECT_DEPENDENCY_RULE = (
    "the work of those missions exists, do not plan it again; "
    "do not write tasks that need what a dependency has not delivered; "
    "when something a task needs is missing in a dependency, say so in the plan notes"
)
#: Shown when a dependency has neither a final report nor a plan summary.
DEPENDENCY_TEXT_FALLBACK = "no report and no plan summary yet"
#: Shown when a dependency has no Live State row.
DEPENDENCY_STATE_FALLBACK = "unknown"
#: The mark left on a text cut to its limit (the mark counts toward the limit).
TRUNCATION_MARK = "..."

ACTIVE_STATE: str = "active"
COMPLETED_STATE: str = "completed"
FAILED_STATE: str = "failed"
DUE_STATE: DependencyStateName = "due"
WAITING_STATE: DependencyStateName = "waiting"
BLOCKED_STATE: DependencyStateName = "blocked"

# colours of the iterative cycle search: every id is coloured at most once
_UNVISITED = 0
_ON_PATH = 1
_CLOSED = 2


class DependencyState(TypedDict):
    """The dependency verdict of one queue entry: state, reason and ids."""

    state: DependencyStateName
    reason: str
    depends_on: list[str]


def build_dependencies(value: Any) -> list[str]:
    """Normalize a raw ``depends_on`` value into a list of mission ids.

    ``None`` or a missing value means no dependencies.  Blank entries and
    duplicates are dropped, the given order is kept, and a self-reference is
    kept so ``validate_dependencies`` can name it.  Anything that is not a
    list of strings raises ``ValueError``, which the board API maps to 400.
    """
    if value is None:
        return []
    if isinstance(value, str):
        raise ValueError("depends_on must be a list of mission ids, not a string")
    if not isinstance(value, (list, tuple)):
        raise ValueError(
            f"depends_on must be a list of mission ids, got {type(value).__name__}"
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"depends_on entry {item!r} must be a mission id string")
        mission_id = item.strip()
        if not mission_id or mission_id in seen:
            continue
        seen.add(mission_id)
        normalized.append(mission_id)
    return normalized


def find_unknown_dependencies(
    dependencies: Sequence[str],
    queue_ids: Iterable[str],
    completed_ids: Iterable[str],
) -> list[str]:
    """The dependency ids that are neither in the queue nor already completed."""
    known = set(queue_ids) | set(completed_ids)
    return [dependency for dependency in dependencies if dependency not in known]


def find_cycle(dependencies_by_id: Mapping[str, Sequence[str]]) -> list[str] | None:
    """The first dependency cycle as a path, e.g. ``["a", "b", "a"]``, or ``None``.

    Iterative depth-first search: an id turns ``_ON_PATH`` when it enters the
    path and ``_CLOSED`` when the path leaves it, and an edge that points at an
    ``_ON_PATH`` id closes a cycle.  Each id is pushed once, so the stack is
    bounded by the number of ids in ``dependencies_by_id``.
    """
    colour: dict[str, int] = {}
    path: list[str] = []
    stack: list[tuple[str, int]] = []
    for start in dependencies_by_id:
        if colour.get(start, _UNVISITED) != _UNVISITED:
            continue
        colour[start] = _ON_PATH
        path.append(start)
        stack.append((start, 0))
        while stack:
            mission_id, index = stack[-1]
            dependencies = dependencies_by_id.get(mission_id, [])
            if index >= len(dependencies):
                stack.pop()
                path.pop()
                colour[mission_id] = _CLOSED
                continue
            stack[-1] = (mission_id, index + 1)
            dependency = dependencies[index]
            state = colour.get(dependency, _UNVISITED)
            if state == _ON_PATH:
                return path[path.index(dependency):] + [dependency]
            if state == _UNVISITED:
                colour[dependency] = _ON_PATH
                path.append(dependency)
                stack.append((dependency, 0))
    return None


def validate_dependencies(
    mission_id: str,
    dependencies: Sequence[str],
    queue_ids: Iterable[str],
    completed_ids: Iterable[str],
    dependencies_by_id: Mapping[str, Sequence[str]],
) -> list[str]:
    """Refusal messages for one mission's proposed ``depends_on``: [] when sound.

    A self-dependency, an unknown id and a cycle each get their own message,
    naming the offending id or the whole cycle path.
    """
    problems: list[str] = []
    if mission_id in dependencies:
        problems.append(f"mission {mission_id} cannot depend on itself")
    for dependency in find_unknown_dependencies(dependencies, queue_ids, completed_ids):
        problems.append(
            f"unknown mission dependency {dependency}: "
            "it is neither in the queue nor completed"
        )
    graph: dict[str, list[str]] = {
        mission: [dependency for dependency in deps if dependency != mission]
        for mission, deps in dependencies_by_id.items()
    }
    graph[mission_id] = [dependency for dependency in dependencies if dependency != mission_id]
    cycle = find_cycle(graph)
    if cycle:
        problems.append("the mission dependency closes a cycle: " + " -> ".join(cycle))
    return problems


def classify_queue(
    entries: Sequence[Mapping[str, Any]],
    state_by_id: Mapping[str, str],
) -> dict[str, DependencyState]:
    """due when every dependency is completed, blocked when one failed, else waiting.

    An entry without dependencies is always due, and a dependency with no Live
    State row counts as unmet, so it makes the entry wait rather than run.
    """
    verdicts: dict[str, DependencyState] = {}
    for entry in entries:
        mission_id = str(entry["mission_id"])
        dependencies = build_dependencies(entry.get("depends_on"))
        if not dependencies:
            verdicts[mission_id] = DependencyState(state=DUE_STATE, reason="", depends_on=[])
            continue
        failed = [
            dependency
            for dependency in dependencies
            if state_by_id.get(dependency) == FAILED_STATE
        ]
        unmet = [
            dependency
            for dependency in dependencies
            if state_by_id.get(dependency) != COMPLETED_STATE
        ]
        if failed:
            reason = f"blocked by {', '.join(failed)}: a dependency failed"
            state: DependencyStateName = BLOCKED_STATE
        elif unmet:
            reason = f"waiting for {', '.join(unmet)}"
            state = WAITING_STATE
        else:
            reason = ""
            state = DUE_STATE
        verdicts[mission_id] = DependencyState(state=state, reason=reason, depends_on=dependencies)
    return verdicts


def _open_dependencies(
    entries: Sequence[Mapping[str, Any]],
    state_by_id: Mapping[str, str] | None,
) -> dict[str, list[str]]:
    """The ``depends_on`` ids of every entry that still sets a queue order.

    A dependency with no queue entry (completed and removed, or never added)
    sets none, and one whose Live State status is ``completed`` does too; with
    no status mapping every queued dependency counts as still open."""
    queued_ids = {str(entry["mission_id"]) for entry in entries}
    dependencies: dict[str, list[str]] = {}
    for entry in entries:
        mission_id = str(entry["mission_id"])
        dependencies[mission_id] = [
            dependency
            for dependency in build_dependencies(entry.get("depends_on"))
            if dependency in queued_ids
            and dependency != mission_id
            and (state_by_id is None or state_by_id.get(dependency, "") != COMPLETED_STATE)
        ]
    return dependencies


def _queue_order(entries: Sequence[Mapping[str, Any]]) -> list[str]:
    """The queue mission ids in position order (the store keeps the entries in
    position order, the sort makes the rule independent of that)."""
    return [
        str(entry["mission_id"])
        for entry in sorted(entries, key=lambda entry: int(entry.get("position", 0)))
    ]


def _order_violations(
    order: Sequence[str], dependencies: Mapping[str, list[str]]
) -> list[tuple[str, str]]:
    """The ``(dependency, mission)`` pairs where a dependency sits after the
    mission that waits on it.

    shortcut: the same rule lives in the untyped ``board_order.violations`` for
    task ids; a typed queue-order helper goes there when both callers need it."""
    position_by_id = {mission_id: index for index, mission_id in enumerate(order)}
    return [
        (dependency, mission_id)
        for mission_id in order
        for dependency in dependencies.get(mission_id, [])
        if dependency in position_by_id and position_by_id[dependency] > position_by_id[mission_id]
    ]


def _dependents_of(
    order: Sequence[str],
    dependencies: Mapping[str, list[str]],
    mission_id: str,
) -> list[str]:
    """Every mission that waits on ``mission_id`` directly or further down the
    chain, in queue order."""
    dependents: dict[str, list[str]] = {id_: [] for id_ in order}
    for id_ in order:
        for dependency in dependencies.get(id_, []):
            dependents.setdefault(dependency, []).append(id_)
    carried = {mission_id}
    stack = [mission_id]
    while stack:  # bounded: an id is pushed once, at most len(order) times
        current = stack.pop()
        for dependent in dependents.get(current, []):
            if dependent not in carried:
                carried.add(dependent)
                stack.append(dependent)
    return [id_ for id_ in order if id_ in carried]


def plan_move_order(
    entries: Sequence[Mapping[str, Any]],
    target_position: int,
    moving_id: str,
    move_dependents: bool = False,
    state_by_id: Mapping[str, str] | None = None,
) -> list[str]:
    """The queue ids in the order one proposed move leaves.

    With ``move_dependents`` the missions that wait on ``moving_id`` ride with it
    as one group, in their existing relative order, landing right after it.  An
    id that is not in the queue leaves the order unchanged: the store raises its
    own ``KeyError`` for it.  The position clamp matches ``move_queue``, which
    clamps to ``[0, n-1]`.
    """
    order = _queue_order(entries)
    if moving_id not in order:
        return order
    group = [moving_id]
    if move_dependents:
        group += _dependents_of(order, _open_dependencies(entries, state_by_id), moving_id)
    ids = set(group)
    rest = [id_ for id_ in order if id_ not in ids]
    block = [id_ for id_ in order if id_ in ids]
    at = min(max(0, target_position), len(rest))
    return rest[:at] + block + rest[at:]


def _new_move_violations(
    entries: Sequence[Mapping[str, Any]],
    target_position: int,
    moving_id: str,
    move_dependents: bool,
    state_by_id: Mapping[str, str] | None,
) -> list[tuple[str, str]]:
    """The ``(dependency, mission)`` pairs the proposed move creates.

    A queue that already breaks a dependency is not blamed for a move that
    changes nothing: only the pairs absent before the move are returned."""
    order = _queue_order(entries)
    if moving_id not in order:
        return []
    dependencies = _open_dependencies(entries, state_by_id)
    planned = plan_move_order(entries, target_position, moving_id, move_dependents, state_by_id)
    before = set(_order_violations(order, dependencies))
    return [pair for pair in _order_violations(planned, dependencies) if pair not in before]


def find_move_problems(
    entries: Sequence[Mapping[str, Any]],
    target_position: int,
    moving_id: str,
    move_dependents: bool = False,
    state_by_id: Mapping[str, str] | None = None,
) -> list[str]:
    """The refusal messages for one proposed queue move: [] when the move is sound.

    A move that lands a mission before a dependency still in the queue names that
    dependency; a move that lands a mission after the missions that depend on it
    names those dependents, unless ``move_dependents`` carries them along.
    """
    problems: list[str] = []
    dependents_named: list[str] = []
    for dependency, mission in _new_move_violations(
        entries, target_position, moving_id, move_dependents, state_by_id
    ):
        if mission == moving_id:
            problems.append(f"mission {moving_id} cannot move before its dependency {dependency}")
        elif dependency == moving_id:
            dependents_named.append(mission)
        else:
            problems.append(
                f"moving {moving_id} leaves {mission} before its dependency {dependency}"
            )
    if dependents_named:
        problems.append(
            f"mission {moving_id} cannot move after the missions that depend on it: "
            f"{', '.join(dependents_named)}"
        )
    return problems


def find_move_blockers(
    entries: Sequence[Mapping[str, Any]],
    target_position: int,
    moving_id: str,
    move_dependents: bool = False,
    state_by_id: Mapping[str, str] | None = None,
) -> list[str]:
    """The ids a refused move is blocked by: the dependencies it would land after,
    and the dependents it would land before, in queue order, without duplicates."""
    blocked: list[str] = []
    for dependency, mission in _new_move_violations(
        entries, target_position, moving_id, move_dependents, state_by_id
    ):
        blocker = mission if dependency == moving_id else dependency
        if blocker not in blocked:
            blocked.append(blocker)
    return blocked


def find_first_due(
    entries: Sequence[Mapping[str, Any]],
    state_by_id: Mapping[str, str],
) -> Mapping[str, Any] | None:
    """The first queue entry whose dependencies are all met, in position order.

    Mirrors ``queue_board.py`` ``next_mission``: an ``active`` mission resumes
    first wherever its position is, then the first ``due`` entry; an entry
    already completed is skipped because there is nothing to run.
    """
    ordered = sorted(entries, key=lambda entry: entry["position"])
    for entry in ordered:
        if state_by_id.get(str(entry["mission_id"]), "") == ACTIVE_STATE:
            return entry
    verdicts = classify_queue(ordered, state_by_id)
    for entry in ordered:
        mission_id = str(entry["mission_id"])
        if state_by_id.get(mission_id, "") == COMPLETED_STATE:
            continue
        if mission_id in verdicts and verdicts[mission_id]["state"] == DUE_STATE:
            return entry
    return None


def find_part_order_problems(
    parts: Sequence[Sequence[str]],
    tasks_by_part: Mapping[int, Sequence[Mapping[str, Any]]],
) -> list[str]:
    """The lint messages for a split plan that uses a later part's output: [] when sound.

    Design: ``docs/mission-dependencies.md`` section (g).  ``parts[k]`` is the task ids of
    part ``k + 1`` and ``tasks_by_part[part_number]`` is those tasks, each with ``id``,
    ``dependencies``, ``scope`` and ``produced`` (the ids or files the task creates; an
    absent ``produced`` means only the task's own id).  A task of part K that names, in its
    dependencies or its scope, an id or file whose producers are all in parts later than K
    gets one message naming the task, its part, the item and the producing part.  An item
    no task produces is no problem: the lint reports only what the plan proves.
    """
    part_of_task: dict[str, int] = {}
    for part_number, task_ids in enumerate(parts, start=1):
        for task_id in task_ids:
            part_of_task[str(task_id)] = part_number

    producer_parts: dict[str, set[int]] = {}
    for part_number, tasks in tasks_by_part.items():
        for task in tasks:
            task_id = str(task.get("id") or "")
            if not task_id:
                continue
            own_part = part_of_task.get(task_id, part_number)
            producer_parts.setdefault(task_id, set()).add(own_part)
            for item in task.get("produced") or []:
                producer_parts.setdefault(str(item), set()).add(own_part)

    problems: list[str] = []
    named_once: set[tuple[str, str]] = set()
    for part_number, tasks in tasks_by_part.items():
        for task in tasks:
            task_id = str(task.get("id") or "")
            own_part = part_of_task.get(task_id, part_number)
            declared = [str(item) for item in (task.get("dependencies") or [])]
            declared += [str(item) for item in (task.get("scope") or [])]
            for item in declared:
                if item == task_id:
                    continue
                producers = producer_parts.get(item)
                if not producers:
                    continue
                producing_part = min(producers)
                if producing_part > own_part and (task_id, item) not in named_once:
                    named_once.add((task_id, item))
                    problems.append(
                        f"task {task_id} of part {own_part} names {item}, "
                        f"which is produced only in part {producing_part}"
                    )
    return problems


def _cut(text: str, max_chars: int) -> str:
    """``text`` cut to ``max_chars`` characters, the cut marked with
    ``TRUNCATION_MARK`` (the mark itself counts toward the limit)."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(TRUNCATION_MARK)] + TRUNCATION_MARK


def build_dependency_context(
    mission: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    state_by_id: Mapping[str, str],
) -> str:
    """The Architect block for one mission's dependencies, "" when it has none.

    ``mission`` is the mission whose ``depends_on`` gives the ids in the stored
    order; ``entries`` are the rows of those dependencies as the caller read them
    (``mission_id``, ``name``, ``report`` -- the final report text, empty when the
    mission produced none -- and ``summary``, the plan summary); ``state_by_id`` is
    the stored Live State status of every mission.  A dependency with no row of its
    own is still named by id.  The report and the summary are carried as data: the
    builder reads no instruction from them.  Each text is cut to
    ``DEPENDENCY_REPORT_MAX_CHARS`` and the whole block to ``ARCHITECT_BLOCK_MAX_CHARS``.
    """
    dependencies = build_dependencies(mission.get("depends_on"))
    if not dependencies:
        return ""
    rows = {str(entry.get("mission_id")): entry for entry in entries}
    lines: list[str] = [DEPENDENCY_BLOCK_HEADING]
    for dependency in dependencies:
        row = rows.get(dependency, {})
        name = str(row.get("name") or dependency)
        state = state_by_id.get(dependency) or DEPENDENCY_STATE_FALLBACK
        text = str(row.get("report") or row.get("summary") or DEPENDENCY_TEXT_FALLBACK)
        detail = _cut(text, DEPENDENCY_REPORT_MAX_CHARS)
        lines.append(f'- {dependency} "{name}" [{state}]: {detail}')
    lines.append(f"Rule: {ARCHITECT_DEPENDENCY_RULE}")
    return _cut("\n".join(lines), ARCHITECT_BLOCK_MAX_CHARS)
