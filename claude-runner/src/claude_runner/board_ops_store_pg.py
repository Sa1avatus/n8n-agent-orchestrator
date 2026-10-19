"""PostgreSQL store for the board ops tables (``board.commands``,
``board.agent``, ``board.settings``, ``board.queue_log``,
``board.host_snapshot``).

Same connection conventions as :class:`board_pg.PgBoardStore`: one
connection, no autocommit; every write runs in a transaction and follows the
audit order — lock the row (``SELECT ... FOR UPDATE``) where the protocol
requires it, compare the version, write the changelog row first, then apply
the change.  The DSN comes from ``DASHBOARD_BOARD_PG_DSN``; when it is unset
(or the connection fails) :func:`get_ops_store` returns an
:class:`UnavailableOpsStore` whose ``status()`` explains the reason — the DSN
itself is never printed, logged or stored beyond the connection object.

All SQL is parameterized (``%s`` / named placeholders); the fixed table
identifiers come from ``docs/board-ops-schema.sql`` and are never built from
values.  The timestamp stamp is always ``now()`` (the connection's clock,
which the in-memory store's ``board.now`` matches).
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from . import board
from .board import ConflictError, now
from .board_ops_store import (
    AgentHeartbeat,
    Command,
    OpsStore,
    Setting,
)
from .board_pg import DSN_ENV, _redact_dsn

__all__ = [
    "PgOpsStore",
    "UnavailableOpsStore",
    "get_ops_store",
]


def _read_json(value: Any) -> Any:
    """Parse a jsonb column (``None`` stays ``None``)."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _jsonb(value: Any) -> Any:
    """Wrap a dict/list value so psycopg binds it as jsonb (None stays None)."""
    if value is None or isinstance(value, str):
        return value
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def _command_row(r: dict[str, Any]) -> Command:
    return Command(
        id=int(r["id"]),
        action=r["action"],
        args_json=_read_json(r["args_json"]),
        status=r["status"],
        requested_by=r["requested_by"],
        requested_at=r["requested_at"].isoformat()
        if hasattr(r["requested_at"], "isoformat")
        else str(r["requested_at"]),
        started_at=r["started_at"].isoformat()
        if r["started_at"] is not None and hasattr(r["started_at"], "isoformat")
        else r["started_at"],
        finished_at=r["finished_at"].isoformat()
        if r["finished_at"] is not None and hasattr(r["finished_at"], "isoformat")
        else r["finished_at"],
        result_json=_read_json(r["result_json"]),
        confirm_token=r["confirm_token"],
    )


class UnavailableOpsStore(OpsStore):
    """Ops store with no backend.

    Mirrors :class:`board_pg.UnavailableBoardStore`: ``status()`` reports
    ``("unavailable", reason)`` and every read raises :class:`RuntimeError`
    with the same reason; every write is refused with the same reason so no
    data is silently lost.  The reason never carries the DSN."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def status(self) -> tuple[str, str]:
        return ("unavailable", self._reason)

    def _refuse(self) -> None:
        raise RuntimeError(f"board ops unavailable: {self._reason}")

    # ---- reads ---------------------------------------------------------------
    def list_commands(
        self,
        action: str | None = None,
        status: str | None = None,
        requested_by: str | None = None,
        since: str | None = None,
        limit: int = 200,
    ) -> list[Command]:
        self._refuse()
        return []

    def count_recent(self, seconds: float) -> int:
        self._refuse()
        return 0

    def get_agent(self) -> AgentHeartbeat | None:
        self._refuse()
        return None

    def get_setting(self, key: str) -> Setting | None:
        self._refuse()
        return None

    def get_queue_log(self, limit: int = 1000) -> list[dict[str, Any]]:
        self._refuse()
        return []

    def get_snapshot(self, kind: str, limit: int = 100) -> list[dict[str, Any]]:
        self._refuse()
        return []

    # ---- writes ---------------------------------------------------------------
    def insert_command(
        self,
        action: str,
        args: dict[str, Any] | None,
        requested_by: str | None,
        confirm_token: str | None = None,
    ) -> Command:
        self._refuse()
        raise AssertionError("unreachable")

    def claim_next(self) -> Command | None:
        self._refuse()
        return None

    def finish_command(
        self, command_id: int, status: str, result: dict[str, Any] | None
    ) -> Command:
        self._refuse()
        raise AssertionError("unreachable")

    def reject_command(self, command_id: int, reason: str) -> Command:
        self._refuse()
        raise AssertionError("unreachable")

    def mark_stale(self, timeout_s: float) -> list[Command]:
        self._refuse()
        return []

    def put_heartbeat(
        self, version: str | None, host_info: dict[str, Any] | None
    ) -> AgentHeartbeat:
        self._refuse()
        raise AssertionError("unreachable")

    def put_setting(
        self, key: str, value: Any, expected_version: str | None, who: str = ""
    ) -> Setting:
        self._refuse()
        raise AssertionError("unreachable")

    def changelog(self, limit: int = 200) -> list[board.ChangelogEntry]:
        self._refuse()
        return []


class PgOpsStore(OpsStore):
    """PostgreSQL-backed board ops store.

    One connection, no autocommit: every write runs in a transaction in the
    audit order — lock the row (``SELECT ... FOR UPDATE``) where the protocol
    requires it, compare the version, write the ``board.changelog`` row
    first, then apply the change.  Reads run outside a transaction.

    ``claim_next`` is the atomic claim from ``docs/board-ops.md`` "Command
    protocol":

    .. code-block:: sql

        BEGIN;
        SELECT id FROM board.commands WHERE status = 'new'
         ORDER BY requested_at LIMIT 1 FOR UPDATE SKIP LOCKED;
        SELECT count(*) FROM board.commands WHERE status = 'running';
        -- if the count above is > 0: ROLLBACK and skip this poll
        UPDATE board.commands SET status = 'running', started_at = now()
         WHERE id = <claimed id> AND status = 'new';
        COMMIT;

    The ``SKIP LOCKED`` keeps the claim race-free between concurrent polls,
    the running-count check is the single-running-command invariant, and
    the ``AND status = 'new'`` guard makes the update atomic.

    Settings writes use the version check: ``expected_version`` (ISO-8601
    ``updated_at`` of the row as read by the caller) must equal the stored
    stamp, else :class:`ConflictError` — the same class ``board.py`` raises
    (HTTP 409, "план изменился, обновите").
    """

    def __init__(self, dsn: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._lock = threading.RLock()
        self._dsn = dsn
        self._conn = psycopg.connect(dsn, row_factory=dict_row)
        # Reads: board schema + public schema both on the search path.
        self._conn.execute("SET search_path TO board, public")

    # ---- internals ----------------------------------------------------------
    def _audit(
        self,
        action: str,
        before: dict[str, Any] | list[Any] | None,
        after: dict[str, Any] | list[Any] | None,
        mission_id: str | None = None,
        task_key: str | None = None,
        who: str = "",
    ) -> None:
        """Insert the changelog row (before the change lands)."""
        self._conn.execute(
            "INSERT INTO changelog "
            "(at, who, action, mission_id, task_key, before_json, after_json) "
            "VALUES (now(), %s, %s, %s, %s, %s, %s)",
            (
                who or None,
                action,
                mission_id,
                task_key,
                json.dumps(before) if before is not None else None,
                json.dumps(after) if after is not None else None,
            ),
        )

    def _command(self, command_id: int) -> Command:
        row = self._conn.execute(
            "SELECT id, action, args_json, status, requested_by, "
            "requested_at, started_at, finished_at, result_json, confirm_token "
            "FROM commands WHERE id = %s",
            (command_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"command {command_id!r} not found")
        return _command_row(row)

    # ---- commands --------------------------------------------------------------
    def insert_command(
        self,
        action: str,
        args: dict[str, Any] | None,
        requested_by: str | None,
        confirm_token: str | None = None,
    ) -> Command:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    "INSERT INTO commands "
                    "(action, args_json, status, requested_by, requested_at, "
                    "confirm_token) VALUES (%s, %s, %s, %s, now(), %s) "
                    "RETURNING id, requested_at",
                    (action, _jsonb(args), "new", requested_by, confirm_token),
                ).fetchone()
                command = Command(
                    id=int(row["id"]),
                    action=action,
                    args_json=args if args is not None else None,
                    status="new",
                    requested_by=requested_by,
                    requested_at=_iso(row["requested_at"]),
                    confirm_token=confirm_token,
                )
                self._audit(
                    "command insert",
                    before=None,
                    after=command.model_dump(),
                    mission_id=str(command.id),
                )
            return command

    def claim_next(self) -> Command | None:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    "SELECT id FROM commands WHERE status = %s "
                    "ORDER BY requested_at LIMIT 1 FOR UPDATE SKIP LOCKED",
                    ("new",),
                ).fetchone()
                if row is None:
                    return None
                running = self._conn.execute(
                    "SELECT count(*) FROM commands WHERE status = %s",
                    ("running",),
                ).fetchone()["count"]
                if running > 0:
                    return None  # transaction rolls back, claim not applied
                self._conn.execute(
                    "UPDATE commands "
                    "SET status = %s, started_at = now() "
                    "WHERE id = %s AND status = %s",
                    ("running", row["id"], "new"),
                )
                self._audit(
                    "command claim",
                    before=None,
                    after=None,
                    mission_id=str(row["id"]),
                )
            return self._command(row["id"])

    def finish_command(
        self, command_id: int, status: str, result: dict[str, Any] | None
    ) -> Command:
        if status not in ("done", "failed"):
            raise ValueError(f"terminal status must be 'done' or 'failed', got {status!r}")
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    "SELECT id, action, args_json, status, requested_by, "
                    "requested_at, started_at, finished_at, result_json, "
                    "confirm_token FROM commands WHERE id = %s "
                    "FOR UPDATE",
                    (command_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"command {command_id!r} not found")
                if row["status"] != "running":
                    raise ValueError(
                        f"command {command_id!r} is {row['status']!r}, not running"
                    )
                before = _command_row(row)
                self._audit(
                    f"command {status}",
                    before=before.model_dump(),
                    after=None,
                    mission_id=str(command_id),
                )
                self._conn.execute(
                    "UPDATE commands SET status = %s, finished_at = now(), "
                    "result_json = %s WHERE id = %s",
                    (status, _jsonb(result), command_id),
                )
            return self._command(command_id)

    def reject_command(self, command_id: int, reason: str) -> Command:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    "SELECT id, action, args_json, status, requested_by, "
                    "requested_at, started_at, finished_at, result_json, "
                    "confirm_token FROM commands WHERE id = %s "
                    "FOR UPDATE",
                    (command_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"command {command_id!r} not found")
                if row["status"] not in ("new", "running"):
                    raise ValueError(
                        f"command {command_id!r} is {row['status']!r}, "
                        "cannot be rejected"
                    )
                before = _command_row(row)
                self._audit(
                    "command rejected",
                    before=before.model_dump(),
                    after=None,
                    mission_id=str(command_id),
                )
                self._conn.execute(
                    "UPDATE commands SET status = %s, finished_at = now(), "
                    "result_json = %s WHERE id = %s",
                    ("rejected", _jsonb({"reason": reason}), command_id),
                )
            return self._command(command_id)

    def mark_stale(self, timeout_s: float) -> list[Command]:
        with self._lock:
            with self._conn.transaction():
                rows = self._conn.execute(
                    "SELECT id, action, args_json, status, requested_by, "
                    "requested_at, started_at, finished_at, result_json, "
                    "confirm_token FROM commands "
                    "WHERE status = %s AND started_at < now() - make_interval(secs => %s) "
                    "FOR UPDATE",
                    ("running", float(timeout_s)),
                ).fetchall()
                if not rows:
                    return []
                for r in rows:
                    self._audit(
                        "command stale",
                        before=_command_row(r).model_dump(),
                        after=None,
                        mission_id=str(r["id"]),
                    )
                self._conn.execute(
                    "UPDATE commands SET status = %s, finished_at = now(), "
                    "result_json = %s "
                    "WHERE status = %s AND started_at < now() - make_interval(secs => %s)",
                    (
                        "failed",
                        _jsonb({"reason": "timeout"}),
                        "running",
                        float(timeout_s),
                    ),
                )
            return [self._command(r["id"]) for r in rows]

    def list_commands(
        self,
        action: str | None = None,
        status: str | None = None,
        requested_by: str | None = None,
        since: str | None = None,
        limit: int = 200,
    ) -> list[Command]:
        conditions = []
        values: list[Any] = []
        if action is not None:
            conditions.append("action = %s")
            values.append(action)
        if status is not None:
            conditions.append("status = %s")
            values.append(status)
        if requested_by is not None:
            conditions.append("requested_by = %s")
            values.append(requested_by)
        if since is not None:
            conditions.append("requested_at >= %s")
            values.append(since)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = self._conn.execute(
            "SELECT id, action, args_json, status, requested_by, "
            "requested_at, started_at, finished_at, result_json, confirm_token "
            "FROM commands" + where + " ORDER BY requested_at DESC, id DESC LIMIT %s",
            values + [limit],
        ).fetchall()
        return [_command_row(r) for r in rows]

    def count_recent(self, seconds: float) -> int:
        row = self._conn.execute(
            "SELECT count(*) FROM commands "
            "WHERE requested_at >= now() - make_interval(secs => %s)",
            (float(seconds),),
        ).fetchone()
        return int(row["count"])

    # ---- agent ---------------------------------------------------------------------
    def get_agent(self) -> AgentHeartbeat | None:
        row = self._conn.execute(
            "SELECT id, heartbeat_at, version, host_info_json "
            "FROM agent ORDER BY heartbeat_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return AgentHeartbeat(
            id=int(row["id"]),
            heartbeat_at=_iso(row["heartbeat_at"]),
            version=row["version"],
            host_info_json=_read_json(row["host_info_json"]),
        )

    def put_heartbeat(
        self, version: str | None, host_info: dict[str, Any] | None
    ) -> AgentHeartbeat:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    "SELECT id, heartbeat_at, version, host_info_json "
                    "FROM agent "
                    "ORDER BY heartbeat_at DESC, id DESC LIMIT 1 FOR UPDATE"
                ).fetchone()
                before = (
                    {
                        "id": int(row["id"]),
                        "heartbeat_at": _iso(row["heartbeat_at"]),
                        "version": row["version"],
                        "host_info_json": _read_json(row["host_info_json"]),
                    }
                    if row is not None
                    else None
                )
                heartbeat = AgentHeartbeat(
                    id=0,
                    heartbeat_at=now(),
                    version=version,
                    host_info_json=dict(host_info) if host_info is not None else None,
                )
                self._audit(
                    "heartbeat",
                    before=before,
                    after=heartbeat.model_dump(),
                )
                row = self._conn.execute(
                    "INSERT INTO agent "
                    "(heartbeat_at, version, host_info_json) "
                    "VALUES (now(), %s, %s) RETURNING id",
                    (heartbeat.version, _jsonb(heartbeat.host_info_json)),
                ).fetchone()
                heartbeat.id = int(row["id"])
            return heartbeat

    # ---- settings --------------------------------------------------------------------
    def get_setting(self, key: str) -> Setting | None:
        row = self._conn.execute(
            "SELECT key, value_json, updated_at FROM settings "
            "WHERE key = %s",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return Setting(
            key=row["key"],
            value_json=_read_json(row["value_json"]),
            updated_at=_iso(row["updated_at"]),
        )

    def put_setting(
        self, key: str, value: Any, expected_version: str | None, who: str = ""
    ) -> Setting:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    "SELECT key, value_json, updated_at FROM settings "
                    "WHERE key = %s FOR UPDATE",
                    (key,),
                ).fetchone()
                if row is None:
                    if expected_version is not None:
                        raise ConflictError(
                            f"setting {key!r}: expected version {expected_version}, "
                            "got none"
                        )
                    before = None
                else:
                    stored = _iso(row["updated_at"])
                    if expected_version is not None and expected_version != stored:
                        raise ConflictError(
                            f"setting {key!r}: expected version {expected_version}, "
                            f"got {stored}"
                        )
                    before = {
                        "key": row["key"],
                        "value_json": _read_json(row["value_json"]),
                        "updated_at": stored,
                    }
                setting = Setting(key=key, value_json=value, updated_at=now())
                self._audit(
                    "setting put",
                    before=before,
                    after=setting.model_dump(),
                    mission_id=key,
                    who=who,
                )
                self._conn.execute(
                    "INSERT INTO settings "
                    "(key, value_json, updated_at) VALUES (%s, %s, now()) "
                    "ON CONFLICT (key) DO UPDATE SET value_json = %s, "
                    "updated_at = now(), version = version + 1",
                    (key, _jsonb(setting.value_json), _jsonb(setting.value_json)),
                )
            return self.get_setting(key)

    # ---- changelog ---------------------------------------------------------------
    def changelog(self, limit: int = 200) -> list[board.ChangelogEntry]:
        rows = self._conn.execute(
            "SELECT id, at, who, action, mission_id, task_key, "
            "before_json, after_json FROM changelog "
            "ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [
            board.ChangelogEntry(
                id=int(r["id"]),
                at=_iso(r["at"]),
                who=r["who"],
                action=r["action"],
                mission_id=r["mission_id"],
                task_key=r["task_key"],
                before_json=_read_json(r["before_json"]),
                after_json=_read_json(r["after_json"]),
            )
            for r in rows
        ]

    # ---- published data -----------------------------------------------------------------
    def get_queue_log(self, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT at, line FROM queue_log "
            "ORDER BY at DESC, id DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [
            {"at": _iso(r["at"]), "line": r["line"]} for r in rows
        ]

    def get_snapshot(self, kind: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT at, data_json FROM host_snapshot "
            "ORDER BY at DESC, id DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [
            {"at": _iso(r["at"]), "data_json": _read_json(r["data_json"])}
            for r in rows
        ]


def _iso(value: Any) -> str | None:
    """ISO-8601 string of a timestamp column (None stays None)."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
def _unavailable_ops(reason: str) -> UnavailableOpsStore:
    return UnavailableOpsStore(reason)


def get_ops_store(dsn: str | None = None) -> OpsStore:
    """Factory: pick the Pg ops store when the DSN is set and connectable;
    otherwise return an unavailable stub whose ``status()`` explains why.

    ``dsn`` defaults to ``DASHBOARD_BOARD_PG_DSN``.  A driver import failure
    (psycopg missing) or a connection failure both yield the unavailable
    stub — the DSN is never printed in the reason."""
    if dsn is None:
        dsn = os.environ.get(DSN_ENV, "")
    if not dsn:
        return _unavailable_ops(
            "база недоступна: DASHBOARD_BOARD_PG_DSN не задан "
            "(PostgreSQL DSN for the board is not set)"
        )
    try:
        return PgOpsStore(dsn)
    except Exception as exc:  # driver missing (ImportError) or connection failed
        # psycopg's error text may embed the DSN — redact it before reporting.
        message = _redact_dsn(str(exc), dsn)
        return _unavailable_ops(f"база недоступна: {message}")
