"""AHAWR board ops store: commands, agent heartbeat, settings, queue log and
host snapshots.

Follows the ``BoardStore``/``InMemoryBoardStore`` conventions of ``board.py``:
every read returns a copy, settings writes take an ``expected_version`` (ISO-8601
``updated_at`` of the row as read by the caller; ``None`` disables the check)
and a mismatch raises ``ConflictError`` (the same class ``board.py`` uses, so a
stale settings write 409s exactly like a board write), and every write saves
the full previous row to the changelog first.

``claim_next`` is atomic: it claims the oldest ``new`` command only when no
command is ``running`` (the single-running-command invariant, see
``docs/board-ops.md`` "Command protocol"); while anything is running it
returns ``None``.

The time source is injectable (``now_fn``) so tests can drive stale detection
deterministically; the default is ``board.now`` (UTC, microseconds, so
successive writes never stamp the same version).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Callable

from pydantic import BaseModel

from . import board
from .board import ConflictError

__all__ = [
    "AgentHeartbeat",
    "Command",
    "ConflictError",
    "InMemoryOpsStore",
    "OpsStore",
    "Setting",
]


class Command(BaseModel):
    """One row of ``board.commands``.

    ``status`` is one of: ``new``, ``running``, ``done``, ``failed``,
    ``rejected`` (rejected is written at admission or claim time, not after
    execution)."""

    id: int
    action: str
    args_json: dict[str, Any] | None = None
    status: str = "new"
    requested_by: str | None = None
    requested_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result_json: dict[str, Any] | None = None
    confirm_token: str | None = None


class AgentHeartbeat(BaseModel):
    """One row of ``board.agent``: one heartbeat per agent poll, newest first."""

    id: int
    heartbeat_at: str
    version: str | None = None
    host_info_json: dict[str, Any] | None = None


class Setting(BaseModel):
    """One row of ``board.settings``; ``updated_at`` is the optimistic-locking
    version."""

    key: str
    value_json: Any
    updated_at: str


class OpsStore(ABC):
    """Read/write interface over the board ops data.

    Every read returns a copy.  Settings writes take an ``expected_version``
    (ISO-8601 ``updated_at`` of the row as read by the caller); ``None``
    disables the check.  A mismatch raises :class:`ConflictError` (the same
    kind ``board.py`` raises: HTTP 409, "план изменился, обновите").  Every
    write saves the full previous row to the changelog first.
    """

    # ---- commands -----------------------------------------------------------
    @abstractmethod
    def insert_command(
        self,
        action: str,
        args: dict[str, Any] | None,
        requested_by: str | None,
        confirm_token: str | None = None,
    ) -> Command:
        """Insert a command row with ``status = 'new'`` and return it."""

    @abstractmethod
    def claim_next(self) -> Command | None:
        """Atomically claim the oldest ``new`` command, or ``None`` when a
        command is already ``running`` (single-running-command invariant)."""

    @abstractmethod
    def finish_command(
        self, command_id: int, status: str, result: dict[str, Any] | None
    ) -> Command:
        """Move the claimed command to ``done`` or ``failed`` with its result."""

    @abstractmethod
    def reject_command(self, command_id: int, reason: str) -> Command:
        """Mark a command ``rejected`` with the reason in ``result_json``."""

    @abstractmethod
    def mark_stale(self, timeout_s: float) -> list[Command]:
        """Mark ``running`` commands older than ``timeout_s`` as ``failed``
        (reason "timeout") and return them."""

    @abstractmethod
    def list_commands(
        self,
        action: str | None = None,
        status: str | None = None,
        requested_by: str | None = None,
        since: str | None = None,
        limit: int = 200,
    ) -> list[Command]:
        """Commands filtered by ``action`` / ``status`` / ``requested_by`` and
        ``requested_at >= since`` (ISO-8601), newest first, at most ``limit``."""

    @abstractmethod
    def count_recent(self, seconds: float) -> int:
        """Number of command rows with ``requested_at`` in the last ``seconds``
        (includes every status; used for the rate limit)."""

    # ---- agent --------------------------------------------------------------
    @abstractmethod
    def get_agent(self) -> AgentHeartbeat | None:
        """The newest agent heartbeat, or ``None`` when the agent never polled."""

    @abstractmethod
    def put_heartbeat(
        self, version: str | None, host_info: dict[str, Any] | None
    ) -> AgentHeartbeat:
        """Append one heartbeat row (the current time) and return it."""

    # ---- settings -----------------------------------------------------------
    @abstractmethod
    def get_setting(self, key: str) -> Setting | None:
        """One settings row by key, or ``None``."""

    @abstractmethod
    def put_setting(
        self, key: str, value: Any, expected_version: str | None, who: str = ""
    ) -> Setting:
        """Create or update the settings row for ``key`` with optimistic
        locking on ``updated_at`` (mismatch raises :class:`ConflictError`).
        ``who`` is the acting user recorded in the audit row."""

    # ---- changelog --------------------------------------------------------------
    @abstractmethod
    def changelog(self, limit: int = 200) -> list[board.ChangelogEntry]:
        """The newest ``limit`` stored ``board.changelog`` rows, newest first."""

    # ---- published data -----------------------------------------------------
    @abstractmethod
    def get_queue_log(self, limit: int = 1000) -> list[dict[str, Any]]:
        """The newest ``limit`` rows of the published ``real_queue.log`` tail
        (``{"at", "line"}``), newest first."""

    @abstractmethod
    def get_snapshot(self, kind: str, limit: int = 100) -> list[dict[str, Any]]:
        """Published rows of ``board.host_snapshot`` (``{"at", "data_json"}``),
        newest first, at most ``limit``.  ``kind`` is the table kind
        (``"host_snapshot"``); the Pg store maps it to the physical table name."""


class InMemoryOpsStore(OpsStore):
    """Thread-safe in-memory store for the board ops (tests and dry runs).

    Versions are ISO-8601 ``updated_at`` stamps; every write first saves the
    full previous row to the changelog, then applies the change and stamps the
    new version (``board.InMemoryBoardStore`` convention).  The time source is
    injectable: pass ``now_fn`` to drive timestamps deterministically."""

    def __init__(self, now_fn: Callable[[], str] | None = None) -> None:
        self._lock = threading.RLock()
        self._now_fn = now_fn or board.now
        self._commands: dict[int, Command] = {}
        self._command_seq = 0
        self._heartbeats: list[AgentHeartbeat] = []
        self._heartbeat_seq = 0
        self._settings: dict[str, Setting] = {}
        self._queue_log: list[dict[str, Any]] = []
        self._queue_log_seq = 0
        self._snapshots: list[dict[str, Any]] = []
        self._snapshot_seq = 0
        self._changelog: list[board.ChangelogEntry] = []
        self._changelog_seq = 0

    # ---- internal helpers ------------------------------------------------------
    def _now(self) -> str:
        return self._now_fn()

    def _check_version(self, key: str, expected: str | None, stored: str) -> None:
        if expected is not None and expected != stored:
            raise board.ConflictError(
                f"setting {key!r}: expected version {expected}, got {stored}"
            )

    def _audit(
        self,
        action: str,
        before: Any,
        after: Any,
        mission_id: str | None = None,
        who: str = "",
    ) -> None:
        """Save the full previous row to the changelog before the change lands."""
        self._changelog_seq += 1
        self._changelog.append(
            board.ChangelogEntry(
                id=self._changelog_seq,
                at=self._now(),
                who=who or None,
                action=action,
                mission_id=mission_id,
                before_json=before,
                after_json=after,
            )
        )

    # ---- commands ---------------------------------------------------------------
    def insert_command(
        self,
        action: str,
        args: dict[str, Any] | None,
        requested_by: str | None,
        confirm_token: str | None = None,
    ) -> Command:
        with self._lock:
            self._command_seq += 1
            command = Command(
                id=self._command_seq,
                action=action,
                args_json=dict(args) if args is not None else None,
                status="new",
                requested_by=requested_by,
                requested_at=self._now(),
                confirm_token=confirm_token,
            )
            self._audit(
                "command insert", before=None, after=command.model_dump()
            )
            self._commands[command.id] = command
            return command

    def claim_next(self) -> Command | None:
        with self._lock:
            if any(c.status == "running" for c in self._commands.values()):
                return None
            candidates = [
                c for c in self._commands.values() if c.status == "new"
            ]
            if not candidates:
                return None
            oldest = min(candidates, key=lambda c: (c.requested_at, c.id))
            claimed = oldest.model_copy(
                update={"status": "running", "started_at": self._now()}
            )
            self._audit(
                "command claim",
                before=oldest.model_dump(),
                after=claimed.model_dump(),
            )
            self._commands[oldest.id] = claimed
            return claimed

    def _require_command(self, command_id: int) -> Command:
        command = self._commands.get(command_id)
        if command is None:
            raise KeyError(f"command {command_id!r} not found")
        return command

    def finish_command(
        self, command_id: int, status: str, result: dict[str, Any] | None
    ) -> Command:
        with self._lock:
            command = self._require_command(command_id)
            if command.status != "running":
                raise ValueError(
                    f"command {command_id!r} is {command.status!r}, not running"
                )
            if status not in ("done", "failed"):
                raise ValueError(f"terminal status must be 'done' or 'failed', got {status!r}")
            finished = command.model_copy(
                update={"status": status, "finished_at": self._now(), "result_json": dict(result) if result is not None else None}
            )
            self._audit(
                f"command {status}", before=command.model_dump(), after=finished.model_dump()
            )
            self._commands[command_id] = finished
            return finished

    def reject_command(self, command_id: int, reason: str) -> Command:
        with self._lock:
            command = self._require_command(command_id)
            if command.status not in ("new", "running"):
                raise ValueError(
                    f"command {command_id!r} is {command.status!r}, cannot be rejected"
                )
            rejected = command.model_copy(
                update={
                    "status": "rejected",
                    "finished_at": self._now(),
                    "result_json": {"reason": reason},
                }
            )
            self._audit(
                "command rejected", before=command.model_dump(), after=rejected.model_dump()
            )
            self._commands[command_id] = rejected
            return rejected

    def mark_stale(self, timeout_s: float) -> list[Command]:
        with self._lock:
            now_dt = datetime.fromisoformat(self._now())
            stale: list[Command] = []
            for command in self._commands.values():
                if command.status != "running":
                    continue
                started = command.started_at
                if started is None:
                    continue
                if (now_dt - datetime.fromisoformat(started)).total_seconds() < timeout_s:
                    continue
                marked = command.model_copy(
                    update={
                        "status": "failed",
                        "finished_at": self._now(),
                        "result_json": {
                            "reason": "timeout"
                        },
                    }
                )
                self._audit(
                    "command stale", before=command.model_dump(), after=marked.model_dump()
                )
                self._commands[command.id] = marked
                stale.append(marked)
            return stale

    def list_commands(
        self,
        action: str | None = None,
        status: str | None = None,
        requested_by: str | None = None,
        since: str | None = None,
        limit: int = 200,
    ) -> list[Command]:
        with self._lock:
            rows = list(self._commands.values())
            if action is not None:
                rows = [c for c in rows if c.action == action]
            if status is not None:
                rows = [c for c in rows if c.status == status]
            if requested_by is not None:
                rows = [c for c in rows if c.requested_by == requested_by]
            if since is not None:
                rows = [c for c in rows if c.requested_at >= since]
            rows.sort(key=lambda c: (c.requested_at, c.id), reverse=True)
            return [c.model_copy() for c in rows[:limit]]

    def count_recent(self, seconds: float) -> int:
        with self._lock:
            now_dt = datetime.fromisoformat(self._now())
            count = 0
            for c in self._commands.values():
                if (now_dt - datetime.fromisoformat(c.requested_at)).total_seconds() <= seconds:
                    count += 1
            return count

    # ---- agent ---------------------------------------------------------------------
    def get_agent(self) -> AgentHeartbeat | None:
        with self._lock:
            if not self._heartbeats:
                return None
            return self._heartbeats[-1].model_copy()

    def put_heartbeat(
        self, version: str | None, host_info: dict[str, Any] | None
    ) -> AgentHeartbeat:
        with self._lock:
            self._heartbeat_seq += 1
            heartbeat = AgentHeartbeat(
                id=self._heartbeat_seq,
                heartbeat_at=self._now(),
                version=version,
                host_info_json=dict(host_info) if host_info is not None else None,
            )
            previous = self._heartbeats[-1].model_dump() if self._heartbeats else None
            self._audit("heartbeat", before=previous, after=heartbeat.model_dump())
            self._heartbeats.append(heartbeat)
            return heartbeat

    # ---- settings --------------------------------------------------------------------
    def get_setting(self, key: str) -> Setting | None:
        with self._lock:
            setting = self._settings.get(key)
            return setting.model_copy() if setting is not None else None

    def put_setting(
        self, key: str, value: Any, expected_version: str | None, who: str = ""
    ) -> Setting:
        with self._lock:
            previous = self._settings.get(key)
            if previous is not None:
                self._check_version(key, expected_version, previous.updated_at)
            else:
                if expected_version is not None:
                    raise board.ConflictError(
                        f"setting {key!r}: expected version {expected_version}, got none"
                    )
            setting = Setting(key=key, value_json=value, updated_at=self._now())
            self._audit(
                "setting put",
                before=previous.model_dump() if previous is not None else None,
                after=setting.model_dump(),
                mission_id=key,
                who=who,
            )
            self._settings[key] = setting
            return setting

    # ---- changelog ----------------------------------------------------------------
    def changelog(self, limit: int = 200) -> list[board.ChangelogEntry]:
        with self._lock:
            return [
                entry.model_copy() for entry in reversed(self._changelog[-limit:])
            ]

    # ---- published data -----------------------------------------------------------------
    def get_queue_log(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in reversed(self._queue_log[-limit:])]

    def get_snapshot(self, kind: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in reversed(self._snapshots[-limit:])]
