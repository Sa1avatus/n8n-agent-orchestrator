"""Parsers for the watcher files the host agent reads (T005 fixtures).

Pure functions from the raw text of a file to the dicts the
``board.host_snapshot`` payload publishes.  Every parser tolerates malformed
lines (a line that does not fit the expected shape is skipped, never raises)
and every parser returns the empty result — ``None`` / ``[]`` / ``0`` — on
empty or unrecognized input.

Formats (``docs/board-ops-notes.md``, "Watchers"):

- ``docker_watchdog.csv``: ``time, engine, engine_ms, n8n, dashboard, llama,
  free_ram_gb``, one probe per 20 s; ``engine`` 1/0, ``12000`` ms = timeout.
- ``docker_vm.csv``: ``time, avail_mb, swap_used_mb, mem_some10, mem_full10,
  io_some10, io_full10`` — the VM memory time series.
- ``queue_watch.state.json``: ``{"q": <bytes in real_queue.log>,
  "d": <bytes in docker_watchdog.log>}``; ``notices_per_hour`` is derived
  from ``real_queue.log`` when that text is passed too.
- ``real_queue.log``: ``started <mission> (run <n>, attempts per task: <a>)``
  and ``ended <mission>: <state>`` per run, ``no progress`` /
  ``REAL QUEUE STOPPED: <reason>`` for protection counters.
- ``mem_guard`` events: one JSON line per killed process (``mem_guard.py:
  MemGuard._report``), possibly prefixed by the runner's own log line.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from typing import Any

from .board_ops_redact import scrub, truncate

__all__ = [
    "parse_mem_guard_events",
    "parse_queue_log",
    "parse_queue_watch_state",
    "parse_vm_csv",
    "parse_watchdog_csv",
]

# ``real_queue.log`` markers.  The lines carry a leading timestamp, so
# the marker is searched, not anchored.
_STARTED_RE = re.compile(r"started\s+(\S+)\s*\(run\s+(\d+)\b")
_ENDED_RE = re.compile(r"ended\s+(\S+):\s*(.*)$")
_NO_PROGRESS_RE = re.compile(r"\bno progress in (\d+) runs\b")

# ``docker_watchdog.csv`` markers: a Docker restart is an incident with
# action ``docker-desktop-restart`` (or ``not-restarting-again`` when the
# watchdog gave up); a probe that finds the engine alive again after a
# stretch of ``engine == 0`` probes is an "engine back" event.  The engine
# is down when its column is ``0`` (or a probe line carries the
# ``engine does not answer`` marker).  ``last_action`` is the newest of
# these events: an "engine back" only when the engine is actually down on
# the probe just before it, a restart incident only when the engine is
# down on that same probe (otherwise the restart row is just a probe).
_ENGINE_DOWN = "engine does not answer"
_ENGINE_ALIVE_ROWS = (["1"], ["1.0"])
_ENGINE_DOWN_ROWS = (["0"], ["0.0"])
_RESTART_ACTIONS = {"docker-desktop-restart", "not-restarting-again"}


def _clean(text: str) -> str:
    """The text with a leading UTF-8 BOM and a trailing newline removed."""
    if not text:
        return ""
    return text.lstrip("﻿").rstrip("\n")


def _rows(text: str) -> list[list[str]]:
    """The CSV data rows (header excluded); a short or broken row is kept
    as-is so the column checks below decide whether it is usable."""
    reader = csv.reader(io.StringIO(_clean(text)))
    try:
        return [list(row) for row in reader]
    except Exception:
        return []


def parse_watchdog_csv(text: str) -> dict[str, Any]:
    """``docker_watchdog.csv``: heartbeat, last action, Docker restarts in
    the last 24 h.

    ``heartbeat`` is the last probe's ``time`` (``None`` without usable rows).
    ``last_action`` is the newest of these events: a ``docker-desktop-restart``
    / ``not-restarting-again`` incident, a VM under-memory warning, or a
    probe that found the engine alive again after a stretch of ``engine == 0``
    probes ("engine back after a failed stretch"); ``None`` when no such
    event is present.  ``restarts_last_24h`` counts restart actions in the
    last 24 h (the newest timestamp is the reference; rows without a
    parseable time are ignored)."""
    rows = _rows(text)
    if not rows:
        return {"heartbeat": None, "last_action": None, "restarts_last_24h": 0}

    last_time: str | None = None
    restarts: list[str] = []
    engine_down = False
    last_action_time: str | None = None
    last_action_kind: str | None = None
    for row in rows:
        if not row or not row[0]:
            continue
        line = ", ".join(row)
        time = row[0]
        try:
            stamp = datetime.fromisoformat(time)
        except ValueError:
            stamp = None
        if stamp is None:
            continue
        if last_time is None or stamp >= datetime.fromisoformat(last_time):
            last_time = time
        action = None
        if "docker-desktop-restart" in line:
            action = "docker-desktop-restart"
        elif "not-restarting-again" in line:
            action = "not-restarting-again"
        elif "VM under memory pressure" in line:
            action = "vm under memory pressure"
        elif engine_down and row[1:2] in _ENGINE_ALIVE_ROWS:
            action = "engine back after a failed stretch"
            engine_down = False
        elif row[1:2] in _ENGINE_DOWN_ROWS or _ENGINE_DOWN in line:
            engine_down = True
        if action is not None:
            last_action_time, last_action_kind = time, action
            if action in _RESTART_ACTIONS:
                restarts.append(time)

    last_stamp = datetime.fromisoformat(last_time) if last_time else None
    restarts_24h = 0
    if last_stamp is not None:
        for stamp in restarts:
            if (last_stamp - datetime.fromisoformat(stamp)).total_seconds() <= 86400:
                restarts_24h += 1
    last_action = last_action_kind
    return {
        "heartbeat": last_time,
        "last_action": last_action,
        "restarts_last_24h": restarts_24h,
    }


def _to_float(value: str | None) -> float | None:
    """A column as a float; ``None`` when the value is missing or not a number."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_vm_csv(text: str) -> list[dict[str, Any]]:
    """``docker_vm.csv``: one time-series point per row, oldest first.

    Keys: ``time``, ``avail_mb``, ``swap_used_mb``, ``mem_some10``,
    ``mem_full10``, ``io_some10``, ``io_full10``.  A row is kept when it
    carries at least a parseable ``time``; a malformed row is skipped."""
    points: list[dict[str, Any]] = []
    for row in _rows(text):
        if not row or not row[0]:
            continue
        stamp = row[0]
        try:
            datetime.fromisoformat(stamp)
        except ValueError:
            continue
        values = [
            _to_float(row[i]) if i < len(row) else None for i in range(7)
        ]
        points.append(
            {
                "time": stamp,
                "avail_mb": values[1],
                "swap_used_mb": values[2],
                "mem_some10": values[3],
                "mem_full10": values[4],
                "io_some10": values[5],
                "io_full10": values[6],
            }
        )
    return points


def parse_queue_watch_state(
    text: str,
    queue_log_text: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """``queue_watch.state.json``: the offsets read so far, plus
    ``notices_per_hour`` and ``last_notifications``.

    ``q`` is the bytes of ``real_queue.log`` already read, ``d`` the bytes
    of ``docker_watchdog.log``.  ``notices_per_hour`` comes from
    ``queue_log_text`` (when passed): the number of ``ended <mission>:`` /
    ``=== real queue done`` / ``ALERT:`` lines whose time is in the last
    hour ending at ``now`` (unparseable times are ignored).
    ``last_notifications`` is the last 10 notice-worthy lines of
    ``queue_log_text`` (``ended <mission>:``, ``ALERT:``,
    ``REAL QUEUE STOPPED:``, ``=== real queue done``), oldest first, each
    ``{"time", "kind", "text"}`` with the text scrubbed and truncated to
    200 characters; it is ``[]`` when no log is passed.  A malformed or
    empty state file gives ``None`` offsets.  Every string result is
    scrubbed via ``board_ops_redact.scrub``."""
    state: dict[str, Any] = {}
    cleaned = _clean(text)
    if cleaned:
        try:
            data = json.loads(cleaned)
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key in ("q", "d"):
                value = data.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    state[key] = int(value)
    notices = 0
    notifications: list[dict[str, str]] = []
    if queue_log_text:
        for line in _clean(queue_log_text).splitlines():
            kind = None
            if "REAL QUEUE STOPPED:" in line:
                kind = "stopped"
            elif "=== real queue done" in line:
                kind = "done"
            elif "ended " in line:
                kind = "ended"
            elif "ALERT:" in line:
                kind = "alert"
            if kind is None:
                continue
            stamp_match = re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", line)
            if now and stamp_match:
                try:
                    stamp = datetime.fromisoformat(stamp_match.group(0) + "+00:00")
                    now_stamp = datetime.fromisoformat(now)
                except ValueError:
                    pass
                else:
                    if 0 <= (now_stamp - stamp).total_seconds() <= 3600:
                        notices += 1
            notifications.append(
                {
                    "time": stamp_match.group(0) if stamp_match else None,
                    "kind": kind,
                    "text": truncate(scrub(line.strip()), 200),
                }
            )
    result = {
        "q": state.get("q"),
        "d": state.get("d"),
        "notices_per_hour": notices,
        "last_notifications": notifications[-10:],
    }
    return {key: scrub(value) if isinstance(value, str) else value for key, value in result.items()}


def _iso_seconds(stamp: str, now: str) -> float | None:
    """The age in seconds of the log stamp ``stamp`` at ``now``.

    Log stamps are naive local time (``YYYY-MM-DD HH:MM:SS``); when
    ``now`` is aware the log side is made aware in UTC before the
    subtraction.  ``None`` when either stamp is not a parseable ISO stamp."""
    try:
        stamp_dt = datetime.fromisoformat(stamp)
        now_dt = datetime.fromisoformat(now)
    except ValueError:
        return None
    if stamp_dt.tzinfo is None and now_dt.tzinfo is not None:
        stamp_dt = stamp_dt.replace(tzinfo=now_dt.tzinfo)
    elif now_dt.tzinfo is None and stamp_dt.tzinfo is not None:
        now_dt = now_dt.replace(tzinfo=stamp_dt.tzinfo)
    return (now_dt - stamp_dt).total_seconds()


def parse_queue_log(text: str, now: str | None = None) -> dict[str, Any]:
    """``real_queue.log``: the current mission and task, the age of the last
    event, runs without progress and consecutive failures.

    ``mission`` is the newest ``started <mission>`` line (no ``ended``
    line after it); ``task`` is its run number.  ``last_event_at`` is the
    newest line that carries a parseable timestamp; ``last_event_age_s`` is
    its age in seconds at ``now`` (``None`` when there is no timestamp).
    ``runs_without_progress`` comes from ``no progress in <n> runs`` lines
    (the biggest count).  ``consecutive_failures`` is the trailing run of
    ``ended <mission>: <state>`` lines with a state other than
    ``completed``, plus each ``REAL QUEUE STOPPED`` line; the count resets
    to 0 on an ``ended <mission>: completed`` line, on a
    ``=== real queue done`` line and on a later ``=== real queue: start``
    line.  Malformed lines are skipped, an empty log gives the empty result."""
    text = _clean(text)
    if not text:
        return {
            "mission": None,
            "task": None,
            "last_event_at": None,
            "last_event_age_s": None,
            "runs_without_progress": 0,
            "consecutive_failures": 0,
        }
    last_event_at: str | None = None
    runs_without_progress = 0
    consecutive_failures = 0
    queue_stopped = False
    # ``ended <mission>:`` closes a run: when it is newer than the newest
    # ``started <mission>`` line the mission is finished.  The log is
    # oldest-first, so the newest ``ended`` line is the last one seen,
    # except when it carries an older stamp.  A ``REAL QUEUE STOPPED`` line
    # stops the queue: when it is present there is no current mission,
    # whatever the earlier ``started`` / ``ended`` lines say.
    last_started: str | None = None
    last_ended: str | None = None
    last_started_mission: str | None = None
    last_started_task: int | None = None
    for line in text.splitlines():
        started = _STARTED_RE.search(line)
        if started:
            stamp_match = re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", line)
            if stamp_match and (last_started is None or stamp_match.group(0) >= last_started):
                last_started = stamp_match.group(0)
                last_started_mission = started.group(1)
                last_started_task = int(started.group(2))
        ended = _ENDED_RE.search(line)
        if ended:
            state = ended.group(2).strip()
            consecutive_failures = 0 if state == "completed" else consecutive_failures + 1
            stamp_match = re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", line)
            if stamp_match and (last_ended is None or stamp_match.group(0) >= last_ended):
                last_ended = stamp_match.group(0)
        no_progress = _NO_PROGRESS_RE.search(line)
        if no_progress:
            runs_without_progress = max(runs_without_progress, int(no_progress.group(1)))
        if "REAL QUEUE STOPPED" in line:
            queue_stopped = True
            consecutive_failures += 1
        if "=== real queue: start" in line:
            queue_restarted = True
        # the failed stretch ends on a completed end, a queue restart or
        # a queue done
        if (
            "=== real queue: start" in line
            or "=== real queue done" in line
            or ended is not None and ended.group(2).strip() == "completed"
        ):
            consecutive_failures = 0
        stamp_match = re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", line)
        if stamp_match and (last_event_at is None or stamp_match.group(0) >= last_event_at):
            last_event_at = stamp_match.group(0)
    age = _iso_seconds(last_event_at, now) if last_event_at is not None and now else None
    mission = None
    task = None
    if not queue_stopped and last_started is not None and (
        last_ended is None or last_started > last_ended
    ):
        mission, task = last_started_mission, last_started_task
    return {
        "mission": mission,
        "task": task,
        "last_event_at": last_event_at,
        "last_event_age_s": age,
        "runs_without_progress": runs_without_progress,
        "consecutive_failures": consecutive_failures,
    }


def _json_events(text: str) -> list[tuple[int, dict[str, Any]]]:
    """The JSON objects of the text, one per line, with their line
    positions (so the caller can keep file order); non-JSON lines are
    skipped.  Lines may carry the runner's own log prefix
    (``memory guard killed pid …:``); the event starts at the first ``{``."""
    events: list[tuple[int, dict[str, Any]]] = []
    for index, line in enumerate(text.splitlines()):
        open_brace = line.find("{")
        if open_brace < 0:
            continue
        try:
            obj = json.loads(line[open_brace:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            events.append((index, obj))
    return events


def parse_mem_guard_events(text: str) -> list[dict[str, Any]]:
    """The ``mem_guard`` events in the text, in order.

    A JSON line ``{"kind": "mem_guard", "pid", "process", "memory_mb",
    "reason", "command"}`` is kept as-is (``kind`` must be ``mem_guard``);
    lines that are not the JSON event (for example the runner's own log
    line, ``memory guard killed pid <pid> (<name>): <reason>``) are parsed
    into the same keys, and a line that fits neither shape is skipped.
    Every string value is scrubbed via ``board_ops_redact.scrub``."""
    pattern = re.compile(r"memory guard killed pid (\d+) \(([^)]+)\): (.*)$")
    found = _json_events(text)
    plain: list[tuple[int, dict[str, Any]]] = []
    for index, line in enumerate(text.splitlines()):
        match = pattern.match(line.strip())
        if not match:
            continue
        pid = int(match.group(1))
        # skip a log line whose event is already present as JSON on this
        # line or later in the file
        if any(pos >= index and event.get("pid") == pid for pos, event in found):
            continue
        plain.append(
            (
                index,
                {
                    "kind": "mem_guard",
                    "pid": pid,
                    "process": match.group(2),
                    "memory_mb": None,
                    "reason": match.group(3),
                    "command": None,
                },
            )
        )
    ordered = sorted(found + plain, key=lambda pair: pair[0])
    return [
        {
            key: scrub(value) if isinstance(value, str) else value
            for key, value in event.items()
        }
        for _, event in ordered
        if event.get("kind") == "mem_guard"
    ]
