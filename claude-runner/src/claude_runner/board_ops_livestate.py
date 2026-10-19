"""Live-state flag view/edit with optimistic locking.

Protocol + implementations for the Telegram control-flag table
(``data_table_user_innMqY8MMiUE9Lrf``, "Autonomous Agent Live State").
``get_flags`` returns the full flag dict and the ``updated_at`` version
stamp; ``set_flags`` applies a whitelist-validated patch guarded by the
version check and writes a changelog entry before the change lands.

The flag whitelist is the set of columns listed in
``docs/board-ops-notes.md`` "Telegram control flags".  ``retry_requested``
and ``accept_requested`` are NOT confirmed columns (see the notes, line 665)
so they are excluded.

Postgres version check: the control-flag table uses ``updated_at`` (text,
ISO-8601) as the optimistic-locking version (``docs/telegram/
control-bot-findings.md:191``).  The UPDATE carries the
``AND "updated_at" = %s`` guard from the notes (line 724); if the row
changed between the read and the update the guarded UPDATE affects zero
rows and a :class:`ConflictError` is raised.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Any

from . import board
from .board import ConflictError, now
from .board_ops_store_pg import _iso

__all__ = [
    "FLAG_WHITELIST",
    "FLAG_TYPES",
    "InMemoryLiveStateStore",
    "LiveStateStore",
    "PgLiveStateStore",
]

# ---------------------------------------------------------------------------
# Flag whitelist (docs/board-ops-notes.md "Telegram control flags", lines 651-663)
# ---------------------------------------------------------------------------

#: Flag name -> allowed Python type (bool for booleans, str for strings,
#: list for the queue array).  ``retry_requested`` and ``accept_requested``
#: are NOT in the notes as confirmed columns (line 665) and are therefore
#: not whitelisted.
FLAG_TYPES: dict[str, type] = {
    "workflow_status": str,
    "owner": str,
    "current_run_id": str,
    "paused": bool,
    "pause_reason": str,
    "steer_note": str,
    "operator_feedback": str,
    "last_control": str,
    "queue": list,
}

#: The set of flag names that may be written; everything else is refused.
FLAG_WHITELIST: frozenset[str] = frozenset(FLAG_TYPES)


class LiveStateStore(ABC):
    """Read/write interface over the Telegram control-flag row for a mission.

    ``get_flags`` returns ``{"flags": dict, "version": str}``.  ``set_flags``
    validates the flag names and types, raises :class:`ConflictError` on a
    version mismatch, writes a changelog entry, and returns the new
    ``{"flags": ..., "version": ...}``.
    """

    @abstractmethod
    def get_flags(self, mission: str) -> dict[str, Any]:
        """The flag dict and the ``updated_at`` version for ``mission``.

        ``{"flags": {...}, "version": "..."}``."""

    @abstractmethod
    def set_flags(
        self,
        mission: str,
        changes: dict[str, Any],
        version: str,
        user: str = "",
    ) -> dict[str, Any]:
        """Apply the whitelisted ``changes`` to ``mission``'s flags.

        Raises :class:`ValueError` for a flag name not in
        :data:`FLAG_WHITELIST` or a value with the wrong type, and
        :class:`ConflictError` when the stored version is not ``version``.
        A changelog entry is written before the flags change.
        Returns the updated ``{"flags": ..., "version": ...}``."""


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------

class InMemoryLiveStateStore(LiveStateStore):
    """Thread-safe in-memory store (tests and dry runs).

    The time source is injectable (``now_fn``) so tests can drive version
    stamps deterministically; the default is ``board.now`` (UTC,
    microseconds, so successive writes never stamp the same version)."""

    _TABLE = "data_table_user_innMqY8MMiUE9Lrf"

    def __init__(self, now_fn: "callable[[], str] | None" = None) -> None:
        self._lock = threading.RLock()
        self._now_fn = now_fn or board.now
        self._rows: dict[str, dict[str, Any]] = {}
        self._changelog: list[board.ChangelogEntry] = []
        self._changelog_seq = 0

    def _now(self) -> str:
        return self._now_fn()

    # ---- reads -----------------------------------------------------------
    def get_flags(self, mission: str) -> dict[str, Any]:
        with self._lock:
            row = self._rows.get(mission)
            if row is None:
                return {"flags": {}, "version": None}
            flags = {k: row[k] for k in FLAG_TYPES if k in row}
            return {"flags": flags, "version": row.get("updated_at")}

    # ---- writes ----------------------------------------------------------
    def set_flags(
        self,
        mission: str,
        changes: dict[str, Any],
        version: str,
        user: str = "",
    ) -> dict[str, Any]:
        _validate_changes(changes)
        with self._lock:
            row = self._rows.get(mission)
            if row is None:
                if version is not None:
                    raise ConflictError(
                        f"live state {mission!r}: expected version {version}, "
                        "got none"
                    )
            else:
                stored = row.get("updated_at")
                if version is not None and version != stored:
                    raise ConflictError(
                        f"live state {mission!r}: expected version {version}, "
                        f"got {stored}"
                    )
            before = {k: row[k] for k in FLAG_TYPES if k in row} if row is not None else None
            after = dict(before or {})
            for k, v in changes.items():
                after[k] = v
            new_version = self._now()
            self._audit(before, after, mission, user)
            if row is None:
                row = {}
            row.update(after)
            row["updated_at"] = new_version
            self._rows[mission] = row
            return {"flags": {k: row[k] for k in FLAG_TYPES if k in row}, "version": new_version}

    # ---- changelog -------------------------------------------------------
    def _audit(
        self,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        mission: str,
        user: str,
    ) -> None:
        self._changelog_seq += 1
        self._changelog.append(
            board.ChangelogEntry(
                id=self._changelog_seq,
                at=self._now(),
                who=user or None,
                action="live state flags set",
                mission_id=mission,
                before_json=before,
                after_json=after,
            )
        )

    def changelog(self, limit: int = 200) -> list[board.ChangelogEntry]:
        with self._lock:
            return [entry.model_copy() for entry in reversed(self._changelog[-limit:])]


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------

class PgLiveStateStore(LiveStateStore):
    """Postgres-backed live-state store.

    One connection, no autocommit; ``set_flags`` runs in a transaction in
    the audit order — compare the version, write the changelog row first,
    then apply the change.  The UPDATE carries the
    ``AND "updated_at" = %s`` optimistic-lock guard from the notes
    (``docs/board-ops-notes.md`` "Exact parameterized SELECT/UPDATE
    statements", line 724); if the row changed since the read the guarded
    UPDATE affects zero rows and a :class:`ConflictError` is raised."""

    _TABLE = "data_table_user_innMqY8MMiUE9Lrf"

    def __init__(self, dsn: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._lock = threading.RLock()
        self._dsn = dsn
        self._conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
        # Reads: board schema + public schema both on the search path.
        self._conn.execute("SET search_path TO board, public")

    # ---- internals ----------------------------------------------------------
    def _audit(
        self,
        action: str,
        before: dict[str, Any] | list[Any] | None,
        after: dict[str, Any] | list[Any] | None,
        mission_id: str | None = None,
        who: str = "",
    ) -> None:
        """Insert the changelog row (before the change lands)."""
        import json

        self._conn.execute(
            "INSERT INTO changelog "
            "(at, who, action, mission_id, task_key, before_json, after_json) "
            "VALUES (now(), %s, %s, %s, %s, %s, %s)",
            (
                who or None,
                action,
                mission_id,
                None,
                json.dumps(before) if before is not None else None,
                json.dumps(after) if after is not None else None,
            ),
        )

    def _select_row(self, state_key: str) -> dict[str, Any] | None:
        """Read one row by ``state_key`` (the UNVERIFIED column list from
        the notes)."""
        row = self._conn.execute(
            'SELECT "state_key", "workflow_status", "owner", "current_run_id", '
            '"paused", "pause_reason", "steer_note", "operator_feedback", '
            '"last_control", "queue", "updated_at" '
            f'FROM "{self._TABLE}" WHERE state_key = %s',
            (state_key,),
        ).fetchone()
        return row

    # ---- reads -----------------------------------------------------------
    def get_flags(self, mission: str) -> dict[str, Any]:
        row = self._select_row(mission)
        if row is None:
            return {"flags": {}, "version": None}
        flags = {k: row[k] for k in FLAG_TYPES if k in row}
        return {"flags": flags, "version": _iso(row["updated_at"])}

    # ---- writes ----------------------------------------------------------
    def set_flags(
        self,
        mission: str,
        changes: dict[str, Any],
        version: str,
        user: str = "",
    ) -> dict[str, Any]:
        _validate_changes(changes)
        with self._lock:
            with self._conn.transaction():
                row = self._select_row(mission)
                if row is None:
                    if version is not None:
                        raise ConflictError(
                            f"live state {mission!r}: expected version {version}, "
                            "got none"
                        )
                    before = None
                else:
                    stored = _iso(row["updated_at"])
                    if version is not None and version != stored:
                        raise ConflictError(
                            f"live state {mission!r}: expected version {version}, "
                            f"got {stored}"
                        )
                    before = {k: row[k] for k in FLAG_TYPES if k in row}
                after = dict(before or {})
                for k, v in changes.items():
                    after[k] = v
                self._audit(
                    "live state flags set",
                    before=before,
                    after=after,
                    mission_id=mission,
                    who=user,
                )
                # Build the parameterized UPDATE with the optimistic-lock
                # guard: the row is only updated if its "updated_at" still
                # equals the version the caller read.
                set_clauses: list[str] = []
                values: list[Any] = []
                for k, v in changes.items():
                    set_clauses.append(f'"{k}" = %s')
                    values.append(v)
                set_clauses.append('"updated_at" = %s')
                new_version = now()
                values.append(new_version)
                values.append(mission)
                if version is not None:
                    values.append(version)
                    guard = ' AND "updated_at" = %s'
                else:
                    guard = ""
                sql = (
                    f'UPDATE "{self._TABLE}" SET '
                    + ", ".join(set_clauses)
                    + f" WHERE state_key = %s" + guard
                )
                result = self._conn.execute(sql, tuple(values))
                if result.rowcount == 0:
                    raise ConflictError(
                        f"live state {mission!r}: expected version {version}, "
                        f"got {stored if row is not None else 'none'}"
                    )
        return self.get_flags(mission)


def _validate_changes(changes: dict[str, Any]) -> None:
    """Raise :class:`ValueError` for a flag name not in the whitelist or a
    value with the wrong type."""
    if not changes:
        return
    for name, value in changes.items():
        if name not in FLAG_WHITELIST:
            known = ", ".join(sorted(FLAG_WHITELIST))
            raise ValueError(
                f"flag {name!r} is not a known live-state flag "
                f"(known flags: {known})"
            )
        expected_type = FLAG_TYPES[name]
        if not isinstance(value, expected_type):
            if expected_type is bool:
                raise ValueError(
                    f"flag {name!r} must be a bool, got {type(value).__name__}"
                )
            raise ValueError(
                f"flag {name!r} must be a {expected_type.__name__}, "
                f"got {type(value).__name__}"
            )
