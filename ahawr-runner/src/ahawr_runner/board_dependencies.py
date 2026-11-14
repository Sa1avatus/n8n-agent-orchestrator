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
