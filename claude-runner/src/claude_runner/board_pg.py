"""PostgreSQL store for the board (``board.*`` schema + the four n8n data tables).

Driver: ``psycopg[binary]`` (see ``docs/board-design.md``, section 8).  The DSN
comes from ``DASHBOARD_BOARD_PG_DSN``; when it is unset, or the connection
fails, :func:`get_board_store` returns an :class:`UnavailableBoardStore`
whose ``status()`` explains the reason — the DSN itself is never printed,
logged or stored beyond the connection object.

Writes run in a transaction on the single connection: lock the row
(``SELECT ... FOR UPDATE``), compare the version, write the changelog row
first, then apply the change.  All SQL is parameterized (``%s`` / named
placeholders); the fixed table identifiers come from ``docs/board-schema.sql``
and are never built from values.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from .board import (
    _PROVIDER_FIELDS,
    _PROVIDER_MODEL_FIELDS,
    BoardStore,
    ChangelogEntry,
    ConflictError,
    LiveState,
    Mission,
    QueueEntry,
    _picked,
    now,
)

__all__ = [
    "DSN_ENV",
    "PgBoardStore",
    "UnavailableBoardStore",
    "get_board_store",
]

DSN_ENV = "DASHBOARD_BOARD_PG_DSN"

# Physical names of the four n8n data tables (public schema), per
# docs/board-data-model.md.  Fixed identifiers, never interpolated from values.
_MISSIONS_TABLE = '"data_table_user_hYAqO52ZGHXxhhHX"'  # quoted: the physical names are mixed case
_LIVE_STATE_TABLE = '"data_table_user_Yn9lOhVhQ9gFymNs"'  # quoted: the physical names are mixed case
_EVENT_LOG_TABLE = '"data_table_user_ceJ7PFzVJvPN0LsV"'  # quoted: the physical names are mixed case
_HERMES_TABLE = '"data_table_user_1S21PsMqT0FpR7eD"'  # quoted: the physical names are mixed case
_HERMES_PROFILE = "claude-code"  # the profile row the workflow of the board uses (the table holds several)

# Fixed schema/table identifiers used verbatim in SQL (not user values).
_QUEUE_TABLE = "queue"
_CHANGELOG_TABLE = "changelog"
_FLAGS_TABLE = "flags"
_TEMPLATES_TABLE = "templates"
_MISSION_CONFIG_TABLE = "mission_config"
_TASK_OVERRIDES_TABLE = "task_overrides"
_PREFLIGHT_TABLE = "preflight"
_PROJECT_RULES_TABLE = "project_rules"
_PROJECT_RULES_LOCK = 7340201
_I18N_LANGUAGES_TABLE = "i18n_languages"
_I18N_STRINGS_TABLE = "i18n_strings"
_PROVIDERS_TABLE = "providers"
_PROVIDER_MODELS_TABLE = "provider_models"
_PROVIDERS_LOCK = 7340202
_PROVIDER_SELECT = ", ".join(("name", *_PROVIDER_FIELDS, "updated_at"))
_MODEL_SELECT = ", ".join(
    ("provider", "model", "probe_result", "probed_at", *_PROVIDER_MODEL_FIELDS, "updated_at")
)
_MODEL_JSON_COLUMNS = ("capability_overrides", "probe_result")

_DDL = [
    "CREATE SCHEMA IF NOT EXISTS board",
    "CREATE TABLE IF NOT EXISTS board.templates ("
    "name text PRIMARY KEY, source_mission_id text, "
    "config_json text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS board.queue ("
    "position integer PRIMARY KEY, mission_id text NOT NULL UNIQUE, "
    "enabled boolean NOT NULL DEFAULT true, attempts_per_task double precision, "
    "retry_failed boolean NOT NULL DEFAULT false, note text)",
    "CREATE TABLE IF NOT EXISTS board.mission_config ("
    "mission_id text PRIMARY KEY, config_json text NOT NULL, "
    "updated_at timestamptz NOT NULL DEFAULT now(), updated_by text)",
    "CREATE TABLE IF NOT EXISTS board.task_overrides ("
    "mission_id text NOT NULL, task_key text NOT NULL, hint text, "
    "extra_json text, updated_at timestamptz NOT NULL DEFAULT now(), "
    "PRIMARY KEY (mission_id, task_key))",
    "CREATE TABLE IF NOT EXISTS board.changelog ("
    "id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(), "
    "who text, action text NOT NULL, mission_id text, task_key text, "
    "before_json text, after_json text)",
    "CREATE TABLE IF NOT EXISTS board.flags ("
    "key text PRIMARY KEY, value text NOT NULL)",
    "CREATE TABLE IF NOT EXISTS board.preflight ("
    "mission_id text NOT NULL, checked_at timestamptz NOT NULL DEFAULT now(), "
    "items_json text NOT NULL, PRIMARY KEY (mission_id, checked_at))",
]


class UnavailableBoardStore(BoardStore):
    """Board store with no backend.

    ``status()`` reports ``("unavailable", reason)`` with a readable reason.
    The reason never contains the DSN (or credentials): when the DSN is
    unset it says so explicitly; when the connection failed, only the driver
    error is summarized, with any host/port/DSN fragment redacted.
    Every read raises :class:`RuntimeError` with the same reason; every
    write is refused with the same reason so no data is silently lost.
    """

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def status(self) -> tuple[str, str]:
        return ("unavailable", self._reason)

    def _refuse(self) -> None:
        raise RuntimeError(f"board unavailable: {self._reason}")

    def get_project_rules(self) -> tuple[list[dict[str, Any]], str]:
        self._refuse()
        return [], ""

    def put_project_rules(
        self, rules: list[dict[str, Any]], expected_version: str | None, actor: str = "",
    ) -> None:
        self._refuse()

    def list_i18n(self) -> dict[str, Any]:
        self._refuse()
        return {"languages": [], "strings": {}}

    def put_i18n_string(self, lang: str, key: str, text: str, actor: str = "") -> None:
        self._refuse()

    def add_i18n_language(self, code: str, name: str) -> None:
        self._refuse()

    def i18n_is_empty(self) -> bool:
        self._refuse()
        return True

    def seed_i18n(self, sets: dict[str, Any]) -> None:
        self._refuse()

    # ---- reads ---------------------------------------------------------------
    def list_missions(self) -> list[Mission]:
        self._refuse()
        return []

    def get_mission(self, mission_id: str) -> Mission | None:
        self._refuse()
        return None

    def list_live_states(self) -> list[LiveState]:
        self._refuse()
        return []

    def get_live_state(self, state_key: str) -> LiveState | None:
        self._refuse()
        return None

    def events(self, state_key: str) -> list[dict[str, Any]]:
        self._refuse()
        return []

    def hermes_config(self) -> dict[str, Any]:
        self._refuse()
        return {}

    def queue(self) -> list[QueueEntry]:
        self._refuse()
        return []

    def mission_config(self, mission_id: str) -> dict[str, Any] | None:
        self._refuse()
        return None

    def task_overrides(self, mission_id: str) -> list[dict[str, Any]]:
        self._refuse()
        return []

    def templates(self) -> list[dict[str, Any]]:
        self._refuse()
        return []

    def flags(self) -> dict[str, str]:
        self._refuse()
        return {}

    def preflight(self, mission_id: str) -> list[dict[str, Any]]:
        self._refuse()
        return []

    def changelog(self, limit: int = 200) -> list[ChangelogEntry]:
        self._refuse()
        return []

    # ---- writes ---------------------------------------------------------------
    def update_mission(
        self, mission_id: str, expected_version: str | None, **fields: Any
    ) -> Mission:
        self._refuse()
        raise AssertionError("unreachable")

    def upsert_live_state(
        self, state_key: str, expected_version: str | None, **fields: Any
    ) -> LiveState:
        self._refuse()
        raise AssertionError("unreachable")

    def append_event(self, state_key: str, event: dict[str, Any]) -> None:
        self._refuse()

    def update_hermes_config(
        self, expected_version: str | None, **fields: Any
    ) -> dict[str, Any]:
        self._refuse()
        raise AssertionError("unreachable")

    def set_queue(
        self, entries: list[QueueEntry], expected_version: str | None
    ) -> list[QueueEntry]:
        self._refuse()
        raise AssertionError("unreachable")

    def add_queue(
        self,
        mission_id: str,
        expected_version: str | None,
        position: int | None = None,
        enabled: bool = True,
        attempts_per_task: float | None = None,
        retry_failed: bool = False,
        note: str | None = None,
    ) -> list[QueueEntry]:
        self._refuse()
        raise AssertionError("unreachable")

    def remove_queue(
        self, mission_id: str, expected_version: str | None, confirmed: bool = False
    ) -> list[QueueEntry]:
        self._refuse()
        raise AssertionError("unreachable")

    def move_queue(
        self, mission_id: str, to_position: int, expected_version: str | None
    ) -> list[QueueEntry]:
        self._refuse()
        raise AssertionError("unreachable")

    def create_mission_rows(
        self, mission: Mission, live: LiveState, config: dict[str, Any] | None,
        position: int | None = None, note: str | None = None, who: str = "",
    ) -> None:
        self._refuse()

    def set_mission_config(
        self, mission_id: str, config: dict[str, Any], expected_version: str | None,
        who: str = "",
    ) -> None:
        self._refuse()

    def set_task_override(
        self, mission_id: str, task_key: str, hint: str | None,
        extra: dict[str, Any] | None, expected_version: str | None, who: str = "",
    ) -> None:
        self._refuse()

    def remove_task_override(
        self, mission_id: str, task_key: str, expected_version: str | None
    ) -> None:
        self._refuse()

    def set_template(
        self, name: str, config: dict[str, Any], source_mission_id: str | None = None,
        expected_version: str | None = None,
    ) -> None:
        self._refuse()

    def remove_template(self, name: str, expected_version: str | None) -> None:
        self._refuse()

    def set_flag(self, key: str, value: str, expected_version: str | None) -> None:
        self._refuse()

    def remove_flag(self, key: str, expected_version: str | None) -> None:
        self._refuse()

    def record_preflight(self, mission_id: str, items: list[dict[str, Any]]) -> None:
        self._refuse()


def _read_json(value: Any) -> Any:
    """Parse a JSON text column (``None`` stays ``None``)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None  # a mission that has no plan yet stores an empty string
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _plan_dict(value: Any) -> dict[str, Any] | None:
    """The plan column as a dict (None for an empty, not yet planned or unreadable value)."""
    plan = _read_json(value)
    return plan if isinstance(plan, dict) else None


class PgBoardStore(BoardStore):
    """PostgreSQL-backed board store.

    One connection in autocommit mode (a read never leaves a transaction open: an open one pinned
    the vacuum horizon and bloated the n8n data tables to 150 MB); every write runs in an explicit
    ``transaction()`` block and
    follows the audit order — lock the row (``SELECT ... FOR UPDATE``),
    compare the version, write the changelog row first, then apply the
    change.  Reads run outside a transaction.  Fixed identifiers (schema,
    table names) come from ``docs/board-schema.sql``; every value is passed
    through ``%s`` / named placeholders, never concatenated into SQL.
    """

    _READ_VERSION: str
    _LIVE_STATE_VERSION: str
    _HERMES_VERSION: str

    def __init__(self, dsn: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._lock = threading.RLock()
        self._dsn = dsn
        self._conn = psycopg.connect(
            dsn, row_factory=dict_row, autocommit=True, prepare_threshold=None
        )  # no server-side prepared statements: a changed table schema must not break cached plans
        # Reads: board schema + public schema both on the search path.
        self._conn.execute("SET search_path TO board, public")
        # Version stamps for tables without a per-row version column.
        self._READ_VERSION = self._queue_version()
        self._LIVE_STATE_VERSION = self._row_version(_LIVE_STATE_TABLE, "updatedAt")
        self._HERMES_VERSION = self._row_version(_HERMES_TABLE, "updatedAt")

    # ---- internals ----------------------------------------------------------
    def status(self) -> tuple[str, str]:
        """Unavailable once the connection is closed or broken (the database restarted)."""
        if getattr(self._conn, "closed", False) or getattr(self._conn, "broken", False):
            return ("unavailable", "the database is unavailable: the connection was lost")
        return ("ok", "")

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
            f"INSERT INTO {_CHANGELOG_TABLE} "
            "(at, who, action, mission_id, task_key, before_json, after_json) "
            "VALUES (now(), %s, %s, %s, %s, %s, %s)",
            (
                who or None,
                action,
                mission_id,
                task_key,
                json.dumps(before, default=str) if before is not None else None,
                json.dumps(after, default=str) if after is not None else None,
            ),
        )

    def _queue_version(self) -> str:
        """Version of the whole queue: the latest board.changelog stamp
        (the queue rows carry no per-row version)."""
        row = self._conn.execute(
            f"SELECT max(at) FROM {_CHANGELOG_TABLE}"
        ).fetchone()
        stamp = row["max"] if row and row["max"] is not None else None
        if stamp is None:
            return "1970-01-01T00:00:00+00:00"
        if hasattr(stamp, "isoformat"):
            return stamp.isoformat()
        return str(stamp)

    def _row_version(self, table: str, column: str) -> str:
        """The version of a single-row table's first row ('' when empty)."""
        row = self._conn.execute(
            f'SELECT "{column}" FROM {table} LIMIT 1'
        ).fetchone()
        if row is None:
            return ""
        value = row[column]
        if value is None:
            return ""
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    def _conflict(self, kind: str, key: str, expected: str, stored: str) -> None:
        raise ConflictError(f"{kind} {key!r}: expected version {expected}, got {stored}")

    # ---- reads -----------------------------------------------------------------
    def list_missions(self) -> list[Mission]:
        rows = self._conn.execute(
            f"SELECT mission_id, state_namespace, name, objective, "
            f"working_directory, version, enabled, rules_json, "
            f"acceptance_criteria_json, retrieval_corpora_json, "
            f"\"createdAt\", \"updatedAt\" FROM {_MISSIONS_TABLE} "
            "ORDER BY mission_id"
        ).fetchall()
        missions = []
        for r in rows:
            missions.append(
                Mission(
                    mission_id=r["mission_id"],
                    state_namespace=r["state_namespace"],
                    name=r["name"] or "",
                    objective=r["objective"] or "",
                    working_directory=r["working_directory"],
                    version=r["version"],
                    enabled=bool(r["enabled"]),
                    rules=_read_json(r["rules_json"]) or [],
                    acceptance_criteria=_read_json(r["acceptance_criteria_json"]) or [],
                    retrieval_corpora=_read_json(r["retrieval_corpora_json"]),
                    created_at=_iso(r["createdAt"]),
                    updated_at=_iso(r["updatedAt"]),
                )
            )
        return missions

    def get_mission(self, mission_id: str) -> Mission | None:
        row = self._conn.execute(
            f"SELECT mission_id, state_namespace, name, objective, "
            f"working_directory, version, enabled, rules_json, "
            f"acceptance_criteria_json, retrieval_corpora_json, "
            f"\"createdAt\", \"updatedAt\" FROM {_MISSIONS_TABLE} "
            "WHERE mission_id = %s",
            (mission_id,),
        ).fetchone()
        if row is None:
            return None
        return Mission(
            mission_id=row["mission_id"],
            state_namespace=row["state_namespace"],
            name=row["name"] or "",
            objective=row["objective"] or "",
            working_directory=row["working_directory"],
            version=row["version"],
            enabled=bool(row["enabled"]),
            rules=_read_json(row["rules_json"]) or [],
            acceptance_criteria=_read_json(row["acceptance_criteria_json"]) or [],
            retrieval_corpora=_read_json(row["retrieval_corpora_json"]),
            created_at=_iso(row["createdAt"]),
            updated_at=_iso(row["updatedAt"]),
        )

    def list_live_states(self) -> list[LiveState]:
        rows = self._conn.execute(
            f"SELECT state_key, worker_session_id, plan_json, task_index, "
            f"task_attempt, status, review_feedback, last_review_score, "
            f"last_review_reason, updated_at, reviewer_session_id, "
            f"reviewer_run_id, reviewer_status, worker_run_id, worker_status, "
            f"architect_run_id, architect_status, architect_session_id "
            f"FROM {_LIVE_STATE_TABLE} ORDER BY state_key"
        ).fetchall()
        return [self._live_row(r) for r in rows]

    def get_live_state(self, state_key: str) -> LiveState | None:
        row = self._conn.execute(
            f"SELECT state_key, worker_session_id, plan_json, task_index, "
            f"task_attempt, status, review_feedback, last_review_score, "
            f"last_review_reason, updated_at, reviewer_session_id, "
            f"reviewer_run_id, reviewer_status, worker_run_id, worker_status, "
            f"architect_run_id, architect_status, architect_session_id "
            f"FROM {_LIVE_STATE_TABLE} WHERE state_key = %s",
            (state_key,),
        ).fetchone()
        return self._live_row(row) if row is not None else None

    def _live_row(self, r: dict[str, Any]) -> LiveState:
        return LiveState(
            state_key=r["state_key"],
            worker_session_id=r["worker_session_id"],
            plan=_plan_dict(r["plan_json"]),
            task_index=r["task_index"],
            task_attempt=r["task_attempt"],
            status=r["status"],
            review_feedback=r["review_feedback"],
            last_review_score=r["last_review_score"],
            last_review_reason=r["last_review_reason"],
            updated_at=_iso(r["updated_at"]),
            reviewer_session_id=r["reviewer_session_id"],
            reviewer_run_id=r["reviewer_run_id"],
            reviewer_status=r["reviewer_status"],
            worker_run_id=r["worker_run_id"],
            worker_status=r["worker_status"],
            architect_run_id=r["architect_run_id"],
            architect_status=r["architect_status"],
            architect_session_id=r["architect_session_id"],
        )

    def events(self, state_key: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT state_key, task_id, task_index, task_attempt, event_type, "
            f"worker_run_id, worker_status, worker_output, worker_error, "
            f"reviewer_run_id, reviewer_status, reviewer_output, reviewer_error, "
            f"review_status, review_score, review_reason, review_next_task, "
            f"created_at FROM {_EVENT_LOG_TABLE} "
            "WHERE state_key = %s ORDER BY id",
            (state_key,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mission_times(self) -> dict[str, dict[str, str]]:
        rows = self._conn.execute(
            f'SELECT state_key, min("createdAt") AS first, max("createdAt") AS last '
            f"FROM {_EVENT_LOG_TABLE} GROUP BY state_key"
        ).fetchall()
        return {r["state_key"]: {"first": _iso(r["first"]), "last": _iso(r["last"])} for r in rows}

    def mission_numbers(self) -> dict[str, int]:
        rows = self._conn.execute(f"SELECT mission_id, id FROM {_MISSIONS_TABLE}").fetchall()
        return {r["mission_id"]: int(r["id"]) for r in rows}

    def hermes_config(self) -> dict[str, Any]:
        row = self._conn.execute(
            f"SELECT * FROM {_HERMES_TABLE} WHERE profile_id = %s ORDER BY id LIMIT 1",
            (_HERMES_PROFILE,),
        ).fetchone()
        if row is None:
            row = self._conn.execute(f"SELECT * FROM {_HERMES_TABLE} ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return {}
        return dict(row)

    def queue(self) -> list[QueueEntry]:
        rows = self._conn.execute(
            f"SELECT position, mission_id, enabled, attempts_per_task, "
            f"retry_failed, note FROM {_QUEUE_TABLE} ORDER BY position"
        ).fetchall()
        return [
            QueueEntry(
                position=int(r["position"]),
                mission_id=r["mission_id"],
                enabled=bool(r["enabled"]),
                attempts_per_task=r["attempts_per_task"],
                retry_failed=bool(r["retry_failed"]),
                note=r["note"],
            )
            for r in rows
        ]

    def mission_config(self, mission_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"SELECT config_json, updated_at, updated_by "
            f"FROM {_MISSION_CONFIG_TABLE} WHERE mission_id = %s",
            (mission_id,),
        ).fetchone()
        if row is None:
            return None
        return _read_json(row["config_json"])

    def task_overrides(self, mission_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT mission_id, task_key, hint, extra_json, updated_at "
            f"FROM {_TASK_OVERRIDES_TABLE} WHERE mission_id = %s "
            "ORDER BY task_key",
            (mission_id,),
        ).fetchall()
        return [
            {
                "mission_id": r["mission_id"],
                "task_key": r["task_key"],
                "hint": r["hint"],
                "extra_json": _read_json(r["extra_json"]),
                "updated_at": _iso(r["updated_at"]),
            }
            for r in rows
        ]

    def templates(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT name, source_mission_id, config_json, created_at "
            f"FROM {_TEMPLATES_TABLE} ORDER BY name"
        ).fetchall()
        return [
            {
                "name": r["name"],
                "source_mission_id": r["source_mission_id"],
                "config_json": _read_json(r["config_json"]),
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ]

    def flags(self) -> dict[str, str]:
        rows = self._conn.execute(
            f"SELECT key, value FROM {_FLAGS_TABLE}"
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def preflight(self, mission_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT mission_id, checked_at, items_json FROM {_PREFLIGHT_TABLE} "
            "WHERE mission_id = %s ORDER BY checked_at DESC",
            (mission_id,),
        ).fetchall()
        return [
            {
                "mission_id": r["mission_id"],
                "checked_at": _iso(r["checked_at"]),
                "items_json": _read_json(r["items_json"]),
            }
            for r in rows
        ]

    def changelog(self, limit: int = 200) -> list[ChangelogEntry]:
        rows = self._conn.execute(
            f"SELECT id, at, who, action, mission_id, task_key, "
            f"before_json, after_json FROM {_CHANGELOG_TABLE} "
            "ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [
            ChangelogEntry(
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

    # ---- writes (each in a transaction: lock -> version check -> audit -> update)
    def audit_rollback(
        self,
        row_kind: str,
        key: str,
        before: Any,
        after: Any,
        who: str = "",
    ) -> None:
        """The rollback's own changelog row (before the restored value lands)."""
        before_json = before.model_dump() if hasattr(before, "model_dump") else before
        self._conn.execute(
            f"INSERT INTO {_CHANGELOG_TABLE} "
            "(at, who, action, mission_id, task_key, before_json, after_json) "
            "VALUES (now(), %s, %s, %s, %s, %s, %s)",
            (
                who or None,
                "rollback",
                key,
                None,
                json.dumps(before_json),
                json.dumps(after),
            ),
        )

    def update_mission(
        self, mission_id: str, expected_version: str | None, **fields: Any
    ) -> Mission:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT \"updatedAt\" FROM {_MISSIONS_TABLE} "
                    "WHERE mission_id = %s FOR UPDATE",
                    (mission_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"mission {mission_id!r} not found")
                stored = _iso(row["updatedAt"])
                if expected_version is not None and expected_version != stored:
                    self._conflict("mission", mission_id, expected_version, stored)
                known_mission_cols = {
                    "state_namespace", "name", "objective", "working_directory",
                    "version", "enabled", "rules_json", "acceptance_criteria_json",
                    "retrieval_corpora_json",
                }
                field_names = [
                    n for n in fields
                    if n != "mission_id" and n in known_mission_cols
                ]
                if not field_names:
                    raise ValueError("no updatable fields")
                before = self.get_mission(mission_id)
                self._audit(
                    "mission update",
                    before=before.model_dump() if before else None,
                    after=None,
                    mission_id=mission_id,
                )
                sets = ", ".join(f"{name} = %s" for name in field_names)
                values = [v for n, v in fields.items() if n in field_names]
                values.append(now())
                update_sql = (
                    f"UPDATE {_MISSIONS_TABLE} SET {sets}, "
                    "\"updatedAt\" = %s WHERE mission_id = %s"
                )
                self._conn.execute(update_sql, values + [mission_id])
            return self.get_mission(mission_id)

    def upsert_live_state(
        self, state_key: str, expected_version: str | None, **fields: Any
    ) -> LiveState:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT \"updatedAt\" FROM {_LIVE_STATE_TABLE} "
                    "WHERE state_key = %s FOR UPDATE",
                    (state_key,),
                ).fetchone()
                stored = _iso(row["updatedAt"]) if row else ""
                if expected_version is not None and expected_version != stored:
                    self._conflict("live state", state_key, expected_version, stored)
                known_live_cols = {
                    "worker_session_id", "plan_json", "task_index", "task_attempt",
                    "status", "review_feedback", "last_review_score",
                    "last_review_reason", "updated_at", "reviewer_session_id",
                    "reviewer_run_id", "reviewer_status", "worker_run_id",
                    "worker_status", "architect_run_id", "architect_status",
                    "architect_session_id",
                }
                # The routes pass plan= as the plan object: serialise it to JSON
                # and store it in the plan_json column.
                if "plan" in fields:
                    fields = dict(fields)
                    fields["plan_json"] = json.dumps(fields.pop("plan"))
                update_names = [n for n in fields if n in known_live_cols]
                if row is not None and not update_names:
                    raise ValueError("no updatable fields")
                before = self.get_live_state(state_key)
                self._audit(
                    "live state upsert",
                    before=before.model_dump() if before else {"state_key": state_key},
                    after=None,
                    mission_id=state_key,
                )
                if row is None:
                    data = {"state_key": state_key}
                    data.update({k: v for k, v in fields.items() if k in known_live_cols})
                    data["updatedAt"] = now()
                    cols = ", ".join(
                        f'"{c}"' if c == "updatedAt" else c for c in data
                    )
                    ph = ", ".join("%s" for _ in data)
                    self._conn.execute(
                        f"INSERT INTO {_LIVE_STATE_TABLE} ({cols}) VALUES ({ph})",
                        list(data.values()),
                    )
                else:
                    sets = ", ".join(
                        f'"{name}" = %s' if name == "updatedAt" else f"{name} = %s"
                        for name in update_names
                    )
                    self._conn.execute(
                        f"UPDATE {_LIVE_STATE_TABLE} SET {sets}, \"updatedAt\" = %s "
                        "WHERE state_key = %s",
                        [v for k, v in fields.items() if k in update_names]
                        + [now(), state_key],
                    )
            return self.get_live_state(state_key)

    def append_event(self, state_key: str, event: dict[str, Any]) -> None:
        with self._lock:
            with self._conn.transaction():
                self._audit(
                    "event append",
                    before=None,
                    after=dict(event),
                    mission_id=state_key,
                )
                known_event_cols = {
                    "state_key", "task_id", "task_index", "task_attempt",
                    "event_type", "worker_run_id", "worker_status",
                    "worker_output", "worker_error", "reviewer_run_id",
                    "reviewer_status", "reviewer_output", "reviewer_error",
                    "review_status", "review_score", "review_reason",
                    "review_next_task", "created_at",
                }
                data = {k: v for k, v in event.items() if k in known_event_cols}
                data.setdefault("state_key", state_key)
                cols = ", ".join(c for c in data)
                ph = ", ".join("%s" for _ in data)
                self._conn.execute(
                    f"INSERT INTO {_EVENT_LOG_TABLE} ({cols}) VALUES ({ph})",
                    list(data.values()),
                )

    def update_hermes_config(
        self, expected_version: str | None, **fields: Any
    ) -> dict[str, Any]:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT \"updatedAt\" FROM {_HERMES_TABLE} "
                    "WHERE profile_id = %s ORDER BY id LIMIT 1 FOR UPDATE",
                    (_HERMES_PROFILE,),
                ).fetchone()
                if row is None:
                    raise KeyError("hermes_config row not found")
                stored = _iso(row["updatedAt"])
                if expected_version is not None and expected_version != stored:
                    self._conflict("hermes_config", "hermes_config", expected_version, stored)
                before = self.hermes_config()
                self._audit(
                    "hermes_config update", before=before, after=None
                )
                known_hermes_cols = {
                    "enabled", "architect_provider", "architect_model",
                    "worker_provider", "worker_model", "reviewer_provider",
                    "reviewer_model", "service_provider", "service_model",
                    "max_tasks", "max_attempts_per_task", "architect_max_polls",
                    "worker_max_polls", "reviewer_max_polls", "architect_poll_seconds",
                    "worker_poll_seconds", "reviewer_poll_seconds",
                    "architect_max_retries", "worker_max_retries", "reviewer_max_retries",
                    "retry_delays_json", "retrieval_enabled", "retrieval_url",
                    "retrieval_corpora_json", "retrieval_worker_max_tokens",
                    "retrieval_reviewer_max_tokens", "retrieval_timeout_ms",
                    "retrieval_label", "runner_url", "run_manager_workflow_id",
                    "worker_think_policy", "worker_think_policy_from_task",
                    "worker_think_off_after_seconds", "worker_think_off_after_steps",
                    "roles_json", "features_json",
                }
                sets = ", ".join(
                    f"{name} = %s"
                    for name in fields
                    if name != "profile_id" and name in known_hermes_cols
                )
                self._conn.execute(
                    f"UPDATE {_HERMES_TABLE} SET {sets}, \"updatedAt\" = %s WHERE profile_id = %s",
                    [v for n, v in fields.items()
                     if n != "profile_id" and n in known_hermes_cols] + [now(), _HERMES_PROFILE],
                )
            return self.hermes_config()

    def _lock_queue(self) -> None:
        """Lock the whole queue (every row) for the transaction."""
        self._conn.execute(
            f"SELECT position FROM {_QUEUE_TABLE} ORDER BY position FOR UPDATE"
        )

    def _queue_snapshot(self) -> list[dict[str, Any]]:
        return [dict(e.model_dump()) for e in self.queue()]

    def _queue_audit(self, action: str, before: list[dict[str, Any]]) -> list[QueueEntry]:
        after = self._queue_snapshot()
        self._audit(action, before=before, after=after)
        return self.queue()

    def set_queue(
        self, entries: list[QueueEntry], expected_version: str | None
    ) -> list[QueueEntry]:
        with self._lock:
            with self._conn.transaction():
                self._lock_queue()
                stored = self._queue_version()
                if expected_version is not None and expected_version != stored:
                    self._conflict("queue", "queue", expected_version, stored)
                ids = [e.mission_id for e in entries]
                if len(ids) != len(set(ids)):
                    raise ValueError("queue contains duplicate mission ids")
                before = self._queue_snapshot()
                self._conn.execute(f"DELETE FROM {_QUEUE_TABLE}")
                for index, entry in enumerate(entries):
                    self._conn.execute(
                        f"INSERT INTO {_QUEUE_TABLE} "
                        "(position, mission_id, enabled, attempts_per_task, "
                        "retry_failed, note) VALUES (%s, %s, %s, %s, %s, %s)",
                        (
                            index,
                            entry.mission_id,
                            entry.enabled,
                            entry.attempts_per_task,
                            entry.retry_failed,
                            entry.note,
                        ),
                    )
                self._READ_VERSION = now()
                return self._queue_audit("queue set", before)

    def _shift_positions(self, where: str, params: tuple, delta: int) -> None:
        """Move queue positions by ``delta`` for the rows matching ``where`` without a transient
        duplicate key: park them as negative numbers first (the primary key is checked per row)."""
        self._conn.execute(
            f"UPDATE {_QUEUE_TABLE} SET position = -(position + %s) - 1 WHERE {where}",
            (delta, *params),
        )
        self._conn.execute(
            f"UPDATE {_QUEUE_TABLE} SET position = -position - 1 "
            "WHERE position < 0 AND position > -1000000"
        )

    def add_queue(
        self,
        mission_id: str,
        expected_version: str | None,
        position: int | None = None,
        enabled: bool = True,
        attempts_per_task: float | None = None,
        retry_failed: bool = False,
        note: str | None = None,
    ) -> list[QueueEntry]:
        with self._lock:
            with self._conn.transaction():
                self._lock_queue()
                stored = self._queue_version()
                if expected_version is not None and expected_version != stored:
                    self._conflict("queue", "queue", expected_version, stored)
                row = self._conn.execute(
                    f"SELECT mission_id FROM {_QUEUE_TABLE} WHERE mission_id = %s",
                    (mission_id,),
                ).fetchone()
                if row is not None:
                    raise ValueError(f"mission {mission_id!r} is already in the queue")
                before = self._queue_snapshot()
                n = self._conn.execute(
                    f"SELECT count(*) FROM {_QUEUE_TABLE}"
                ).fetchone()["count"]
                insert_at = n if position is None else max(0, min(position, n))
                # Shift positions down by one for the affected tail.
                self._shift_positions("position >= %s", (insert_at,), 1)
                self._conn.execute(
                    f"INSERT INTO {_QUEUE_TABLE} "
                    "(position, mission_id, enabled, attempts_per_task, "
                    "retry_failed, note) VALUES (%s, %s, %s, %s, %s, %s)",
                    (insert_at, mission_id, enabled, attempts_per_task,
                     retry_failed, note),
                )
                self._READ_VERSION = now()
                return self._queue_audit("queue add", before)

    def remove_queue(
        self, mission_id: str, expected_version: str | None, confirmed: bool = False
    ) -> list[QueueEntry]:
        with self._lock:
            with self._conn.transaction():
                self._lock_queue()
                stored = self._queue_version()
                if expected_version is not None and expected_version != stored:
                    self._conflict("queue", "queue", expected_version, stored)
                row = self._conn.execute(
                    f"SELECT mission_id FROM {_QUEUE_TABLE} WHERE mission_id = %s",
                    (mission_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"mission {mission_id!r} is not in the queue")
                before = self._queue_snapshot()
                self._conn.execute(
                    f"DELETE FROM {_QUEUE_TABLE} WHERE mission_id = %s",
                    (mission_id,),
                )
                # Renumber positions contiguously (park them as negatives first: per-row key check).
                self._conn.execute(f"UPDATE {_QUEUE_TABLE} SET position = -position - 1")
                self._conn.execute(
                    f"WITH ordered AS (SELECT position, mission_id "
                    f"FROM {_QUEUE_TABLE} ORDER BY position) "
                    f"UPDATE {_QUEUE_TABLE} q SET position = o.new_position "
                    f"FROM (SELECT row_number() OVER (ORDER BY -position) - 1 AS new_position, "
                    f"mission_id FROM {_QUEUE_TABLE} ORDER BY position) o "
                    f"WHERE q.mission_id = o.mission_id"
                )
                self._READ_VERSION = now()
                return self._queue_audit("queue remove", before)

    def move_queue(
        self, mission_id: str, to_position: int, expected_version: str | None
    ) -> list[QueueEntry]:
        with self._lock:
            with self._conn.transaction():
                self._lock_queue()
                stored = self._queue_version()
                if expected_version is not None and expected_version != stored:
                    self._conflict("queue", "queue", expected_version, stored)
                row = self._conn.execute(
                    f"SELECT position FROM {_QUEUE_TABLE} WHERE mission_id = %s",
                    (mission_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"mission {mission_id!r} is not in the queue")
                before = self._queue_snapshot()
                n = self._conn.execute(
                    f"SELECT count(*) FROM {_QUEUE_TABLE}"
                ).fetchone()["count"]
                target = max(0, min(to_position, n - 1))
                old_position = int(row["position"])
                if old_position < target:
                    self._conn.execute(
                        f"UPDATE {_QUEUE_TABLE} SET position = -1000000 WHERE mission_id = %s", (mission_id,)
                    )
                    self._shift_positions("position > %s AND position <= %s", (old_position, target), -1)
                elif old_position > target:
                    self._conn.execute(
                        f"UPDATE {_QUEUE_TABLE} SET position = -1000000 WHERE mission_id = %s", (mission_id,)
                    )
                    self._shift_positions("position >= %s AND position < %s", (target, old_position), 1)
                self._conn.execute(
                    f"UPDATE {_QUEUE_TABLE} SET position = %s WHERE mission_id = %s",
                    (target, mission_id),
                )
                self._READ_VERSION = now()
                return self._queue_audit("queue move", before)

    def create_mission_rows(
        self, mission: Mission, live: LiveState, config: dict[str, Any] | None,
        position: int | None = None, note: str | None = None, who: str = "",
    ) -> None:
        with self._lock:
            with self._conn.transaction():
                self._conn.execute(
                    f'INSERT INTO {_MISSIONS_TABLE} '
                    "(mission_id, state_namespace, name, objective, "
                    "working_directory, version, enabled, rules_json, "
                    "acceptance_criteria_json, retrieval_corpora_json, "
                    '"createdAt", "updatedAt") '
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())",
                    (
                        mission.mission_id,
                        mission.state_namespace,
                        mission.name,
                        mission.objective,
                        mission.working_directory,
                        mission.version,
                        mission.enabled,
                        json.dumps(mission.rules),
                        json.dumps(mission.acceptance_criteria),
                        json.dumps(mission.retrieval_corpora)
                        if mission.retrieval_corpora is not None
                        else None,
                    ),
                )
                self._conn.execute(
                    f"INSERT INTO {_LIVE_STATE_TABLE} "
                    "(state_key, worker_session_id, plan_json, task_index, "
                    "task_attempt, status, review_feedback, last_review_score, "
                    "last_review_reason, reviewer_session_id, reviewer_run_id, "
                    "reviewer_status, worker_run_id, worker_status, "
                    "architect_run_id, architect_status, architect_session_id, "
                    '"updatedAt") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '
                    "%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        live.state_key,
                        live.worker_session_id,
                        json.dumps(live.plan) if live.plan is not None else None,
                        live.task_index,
                        live.task_attempt,
                        live.status,
                        live.review_feedback,
                        live.last_review_score,
                        live.last_review_reason,
                        live.reviewer_session_id,
                        live.reviewer_run_id,
                        live.reviewer_status,
                        live.worker_run_id,
                        live.worker_status,
                        live.architect_run_id,
                        live.architect_status,
                        live.architect_session_id,
                        now(),
                    ),
                )
                self._lock_queue()
                n = self._conn.execute(
                    f"SELECT count(*) FROM {_QUEUE_TABLE}"
                ).fetchone()["count"]
                insert_at = n if position is None else max(0, min(position, n))
                self._shift_positions("position >= %s", (insert_at,), 1)
                self._conn.execute(
                    f"INSERT INTO {_QUEUE_TABLE} "
                    "(position, mission_id, enabled, attempts_per_task, "
                    "retry_failed, note) VALUES (%s, %s, %s, %s, %s, %s)",
                    (insert_at, mission.mission_id, True, None, False, note),
                )
                if config:
                    self._conn.execute(
                        f"INSERT INTO {_MISSION_CONFIG_TABLE} "
                        "(mission_id, config_json, updated_at, updated_by) "
                        "VALUES (%s, %s, %s, %s)",
                        (
                            mission.mission_id,
                            json.dumps(config),
                            now(),
                            who or None,
                        ),
                    )
                self._READ_VERSION = now()
                self._audit(
                    "mission create",
                    before=None,
                    after={
                        "mission": mission.model_dump(),
                        "live_state": live.model_dump(),
                        "queue": [dict(e.model_dump()) for e in self.queue()],
                    },
                    mission_id=mission.mission_id,
                    who=who or None,
                )

    def set_mission_config(
        self, mission_id: str, config: dict[str, Any], expected_version: str | None,
        who: str = "",
    ) -> None:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT updated_at FROM {_MISSION_CONFIG_TABLE} "
                    "WHERE mission_id = %s FOR UPDATE",
                    (mission_id,),
                ).fetchone()
                stored = _iso(row["updated_at"]) if row else ""
                if expected_version is not None and expected_version != stored:
                    self._conflict("mission_config", mission_id, expected_version, stored)
                before = self.mission_config(mission_id)
                self._audit(
                    "mission_config set",
                    before=before,
                    after=dict(config),
                    mission_id=mission_id,
                    who=who,
                )
                self._conn.execute(
                    f"INSERT INTO {_MISSION_CONFIG_TABLE} "
                    "(mission_id, config_json, updated_at, updated_by) "
                    "VALUES (%s, %s, %s, %s) "
                    f"ON CONFLICT (mission_id) DO UPDATE SET "
                    "config_json = %s, updated_at = %s, updated_by = %s",
                    (
                        mission_id,
                        json.dumps(config),
                        now(),
                        who or None,
                        json.dumps(config),
                        now(),
                        who or None,
                    ),
                )

    def set_task_override(
        self, mission_id: str, task_key: str, hint: str | None,
        extra: dict[str, Any] | None, expected_version: str | None, who: str = "",
    ) -> None:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT updated_at FROM {_TASK_OVERRIDES_TABLE} "
                    "WHERE mission_id = %s AND task_key = %s FOR UPDATE",
                    (mission_id, task_key),
                ).fetchone()
                stored = _iso(row["updated_at"]) if row else ""
                if expected_version is not None and expected_version != stored:
                    self._conflict(
                        "task_override", f"{mission_id}/{task_key}", expected_version, stored
                    )
                before = None
                if row is not None:
                    before = {
                        "mission_id": mission_id,
                        "task_key": task_key,
                        "hint": hint,
                        "extra_json": extra,
                        "updated_at": stored,
                    }
                self._audit(
                    "task_override set",
                    before=before,
                    after={"mission_id": mission_id, "task_key": task_key,
                           "hint": hint, "extra_json": extra},
                    mission_id=mission_id,
                    task_key=task_key,
                    who=who,
                )
                self._conn.execute(
                    f"INSERT INTO {_TASK_OVERRIDES_TABLE} "
                    "(mission_id, task_key, hint, extra_json, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (mission_id, task_key) DO UPDATE SET "
                    "hint = %s, extra_json = %s, updated_at = %s",
                    (
                        mission_id, task_key, hint,
                        json.dumps(extra) if extra is not None else None, now(),
                        hint,
                        json.dumps(extra) if extra is not None else None, now(),
                    ),
                )

    def remove_task_override(
        self, mission_id: str, task_key: str, expected_version: str | None
    ) -> None:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT updated_at, hint, extra_json FROM {_TASK_OVERRIDES_TABLE} "
                    "WHERE mission_id = %s AND task_key = %s FOR UPDATE",
                    (mission_id, task_key),
                ).fetchone()
                if row is None:
                    raise KeyError(f"task override {mission_id!r}/{task_key!r} not found")
                stored = _iso(row["updated_at"])
                if expected_version is not None and expected_version != stored:
                    self._conflict(
                        "task_override", f"{mission_id}/{task_key}", expected_version, stored
                    )
                before = {
                    "mission_id": mission_id,
                    "task_key": task_key,
                    "hint": row["hint"],
                    "extra_json": _read_json(row["extra_json"]),
                    "updated_at": stored,
                }
                self._audit(
                    "task_override remove",
                    before=before,
                    after=None,
                    mission_id=mission_id,
                    task_key=task_key,
                )
                self._conn.execute(
                    f"DELETE FROM {_TASK_OVERRIDES_TABLE} "
                    "WHERE mission_id = %s AND task_key = %s",
                    (mission_id, task_key),
                )

    def set_template(
        self, name: str, config: dict[str, Any], source_mission_id: str | None = None,
        expected_version: str | None = None,
    ) -> None:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT created_at FROM {_TEMPLATES_TABLE} "
                    "WHERE name = %s FOR UPDATE",
                    (name,),
                ).fetchone()
                stored = _iso(row["created_at"]) if row else ""
                if expected_version is not None and expected_version != stored:
                    self._conflict("template", name, expected_version, stored)
                before = None
                if row is not None:
                    trow = self._conn.execute(
                        f"SELECT name, source_mission_id, config_json "
                        f"FROM {_TEMPLATES_TABLE} WHERE name = %s",
                        (name,),
                    ).fetchone()
                    before = {
                        "name": trow["name"],
                        "source_mission_id": trow["source_mission_id"],
                        "config_json": _read_json(trow["config_json"]),
                    }
                self._audit("template set", before=before, after=dict(config), mission_id=name)
                self._conn.execute(
                    f"INSERT INTO {_TEMPLATES_TABLE} "
                    "(name, source_mission_id, config_json) "
                    "VALUES (%s, %s, %s) "
                    f"ON CONFLICT (name) DO UPDATE SET "
                    "source_mission_id = %s, config_json = %s",
                    (name, source_mission_id, json.dumps(config),
                     source_mission_id, json.dumps(config)),
                )

    def remove_template(self, name: str, expected_version: str | None) -> None:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT created_at FROM {_TEMPLATES_TABLE} "
                    "WHERE name = %s FOR UPDATE",
                    (name,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"template {name!r} not found")
                stored = _iso(row["created_at"])
                if expected_version is not None and expected_version != stored:
                    self._conflict("template", name, expected_version, stored)
                trow = self._conn.execute(
                    f"SELECT name, source_mission_id, config_json "
                    f"FROM {_TEMPLATES_TABLE} WHERE name = %s",
                    (name,),
                ).fetchone()
                before = {
                    "name": trow["name"],
                    "source_mission_id": trow["source_mission_id"],
                    "config_json": _read_json(trow["config_json"]),
                }
                self._audit("template remove", before=before, after=None, mission_id=name)
                self._conn.execute(
                    f"DELETE FROM {_TEMPLATES_TABLE} WHERE name = %s",
                    (name,),
                )

    def set_flag(self, key: str, value: str, expected_version: str | None) -> None:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT value FROM {_FLAGS_TABLE} WHERE key = %s FOR UPDATE",
                    (key,),
                ).fetchone()
                # flags has no version column; the audit uses the previous value.
                before = {"key": key, "value": row["value"]} if row else None
                self._audit("flag set", before=before, after={"key": key, "value": value})
                self._conn.execute(
                    f"INSERT INTO {_FLAGS_TABLE} (key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = %s",
                    (key, value, value),
                )

    def remove_flag(self, key: str, expected_version: str | None) -> None:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT value FROM {_FLAGS_TABLE} WHERE key = %s FOR UPDATE",
                    (key,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"flag {key!r} not found")
                before = {"key": key, "value": row["value"]}
                self._audit("flag remove", before=before, after=None)
                self._conn.execute(
                    f"DELETE FROM {_FLAGS_TABLE} WHERE key = %s",
                    (key,),
                )

    # ---- providers (board.providers, board.provider_models) ----------------------
    def providers_updated_at(self) -> str:
        row = self._conn.execute(
            f"SELECT max(at) FROM {_CHANGELOG_TABLE} WHERE left(action, 9) = 'provider '"
        ).fetchone()
        return _iso(row["max"]) or ""

    def list_providers(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT {_PROVIDER_SELECT} FROM {_PROVIDERS_TABLE} ORDER BY name COLLATE \"C\""
        ).fetchall()
        models = self._conn.execute(
            f"SELECT {_MODEL_SELECT} FROM {_PROVIDER_MODELS_TABLE} "
            'ORDER BY provider COLLATE "C", model COLLATE "C"'
        ).fetchall()
        by_provider: dict[str, list[dict[str, Any]]] = {}
        for model in models:
            by_provider.setdefault(model["provider"], []).append(_model_dict(model))
        return [_provider_dict(r, by_provider.get(r["name"], [])) for r in rows]

    def get_provider(self, name: str) -> dict[str, Any] | None:
        return self._provider_view(name)

    def _provider_view(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"SELECT {_PROVIDER_SELECT} FROM {_PROVIDERS_TABLE} WHERE name = %s", (name,)
        ).fetchone()
        if row is None:
            return None
        models = self._conn.execute(
            f"SELECT {_MODEL_SELECT} FROM {_PROVIDER_MODELS_TABLE} "
            'WHERE provider = %s ORDER BY model COLLATE "C"',
            (name,),
        ).fetchall()
        return _provider_dict(row, [_model_dict(m) for m in models])

    def _require_provider(self, name: str) -> dict[str, Any]:
        view = self._provider_view(name)
        if view is None:
            raise KeyError(f"provider {name!r} not found")
        return view

    def _model_view(self, provider: str, model: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"SELECT {_MODEL_SELECT} FROM {_PROVIDER_MODELS_TABLE} "
            "WHERE provider = %s AND model = %s",
            (provider, model),
        ).fetchone()
        return _model_dict(row) if row is not None else None

    def _require_model(self, provider: str, model: str) -> dict[str, Any]:
        view = self._model_view(provider, model)
        if view is None:
            raise KeyError(f"model {provider!r}/{model!r} not found")
        return view

    def _lock_providers(self) -> None:
        self._conn.execute("SELECT pg_advisory_xact_lock(%s)", (_PROVIDERS_LOCK,))

    def _check_version(self, kind: str, key: str, expected: str | None, stored: str) -> None:
        if expected is not None and expected != stored:
            self._conflict(kind, key, expected, stored)

    def _insert(self, table: str, row: dict[str, Any]) -> None:
        self._conn.execute(
            f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})",
            list(row.values()),
        )

    def _update(
        self, table: str, values: dict[str, Any], where: str, params: tuple[Any, ...]
    ) -> None:
        sets = ", ".join(f"{column} = %s" for column in values)
        self._conn.execute(
            f"UPDATE {table} SET {sets} WHERE {where}", [*values.values(), *params]
        )

    def create_provider(self, name: str, fields: dict[str, Any]) -> None:
        row = {"name": name, "enabled": True, **_picked(fields, _PROVIDER_FIELDS)}
        with self._lock, self._conn.transaction():
            self._lock_providers()
            if self._provider_view(name) is not None:
                raise ConflictError(f"provider {name!r} already exists")
            self._audit("provider create", before=None, after=dict(row))
            self._insert(_PROVIDERS_TABLE, {**row, "updated_at": now()})

    def update_provider(
        self, name: str, fields: dict[str, Any], expected_version: str | None
    ) -> None:
        picked = _picked(fields, _PROVIDER_FIELDS)
        with self._lock, self._conn.transaction():
            self._lock_providers()
            previous = self._require_provider(name)
            self._check_version("provider", name, expected_version, previous["version"])
            before = _columns_of(previous)
            self._audit("provider update", before=before, after={**before, **picked})
            self._update(
                _PROVIDERS_TABLE, {**picked, "updated_at": now()}, "name = %s", (name,)
            )

    def delete_provider(self, name: str, expected_version: str | None) -> None:
        with self._lock, self._conn.transaction():
            self._lock_providers()
            previous = self._require_provider(name)
            self._check_version("provider", name, expected_version, previous["version"])
            self._audit("provider remove", before=previous, after=None)
            self._conn.execute(f"DELETE FROM {_PROVIDERS_TABLE} WHERE name = %s", (name,))

    def add_model(self, provider: str, model: str, fields: dict[str, Any]) -> None:
        with self._lock, self._conn.transaction():
            self._lock_providers()
            self._require_provider(provider)
            if self._model_view(provider, model) is not None:
                raise ConflictError(f"model {provider!r}/{model!r} already exists")
            row = {
                "provider": provider,
                "model": model,
                "supports_thinking": False,
                **_picked(fields, _PROVIDER_MODEL_FIELDS),
                "probe_result": None,
                "probed_at": None,
            }
            self._audit("provider model add", before=None, after=dict(row))
            self._insert(_PROVIDER_MODELS_TABLE, {**_model_db(row), "updated_at": now()})

    def update_model(
        self,
        provider: str,
        model: str,
        fields: dict[str, Any],
        expected_version: str | None,
    ) -> None:
        picked = _picked(fields, _PROVIDER_MODEL_FIELDS)
        with self._lock, self._conn.transaction():
            self._lock_providers()
            previous = self._require_model(provider, model)
            self._check_version(
                "provider model", f"{provider}/{model}", expected_version,
                previous["version"],
            )
            before = _columns_of(previous)
            self._audit(
                "provider model update", before=before, after={**before, **picked}
            )
            self._update(
                _PROVIDER_MODELS_TABLE,
                _model_db({**picked, "updated_at": now()}),
                "provider = %s AND model = %s",
                (provider, model),
            )

    def remove_model(self, provider: str, model: str, expected_version: str | None) -> None:
        with self._lock, self._conn.transaction():
            self._lock_providers()
            previous = self._require_model(provider, model)
            self._check_version(
                "provider model", f"{provider}/{model}", expected_version,
                previous["version"],
            )
            before = _columns_of(previous)
            self._audit("provider model remove", before=before, after=None)
            self._conn.execute(
                f"DELETE FROM {_PROVIDER_MODELS_TABLE} WHERE provider = %s AND model = %s",
                (provider, model),
            )

    def set_model_probe(self, provider: str, model: str, probe_result: dict[str, Any]) -> None:
        with self._lock, self._conn.transaction():
            self._lock_providers()
            before = _columns_of(self._require_model(provider, model))
            probed_at = now()
            self._audit(
                "provider model probe",
                before=before,
                after={**before, "probe_result": probe_result, "probed_at": probed_at},
            )
            self._update(
                _PROVIDER_MODELS_TABLE,
                _model_db({
                    "probe_result": probe_result,
                    "probed_at": probed_at,
                    "updated_at": probed_at,
                }),
                "provider = %s AND model = %s",
                (provider, model),
            )

    def record_preflight(self, mission_id: str, items: list[dict[str, Any]]) -> None:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT items_json FROM {_PREFLIGHT_TABLE} "
                    "WHERE mission_id = %s ORDER BY checked_at DESC LIMIT 1 FOR UPDATE",
                    (mission_id,),
                ).fetchone()
                before = None
                if row is not None:
                    before = {
                        "mission_id": mission_id,
                        "checked_at": _iso(row["items_json"]),
                        "items_json": _read_json(row["items_json"]),
                    }
                self._audit(
                    "preflight record",
                    before=before,
                    after=dict(items),
                    mission_id=mission_id,
                )
                self._conn.execute(
                    f"INSERT INTO {_PREFLIGHT_TABLE} "
                    "(mission_id, checked_at, items_json) "
                    "VALUES (%s, %s, %s)",
                    (mission_id, now(), json.dumps(items)),
                )

    def get_project_rules(self) -> tuple[list[dict[str, Any]], str]:
        rows = self._conn.execute(
            f"SELECT name, pattern, position, updated_at FROM {_PROJECT_RULES_TABLE} "
            "ORDER BY position"
        ).fetchall()
        rules = [
            {"name": r["name"], "pattern": r["pattern"], "position": r["position"]}
            for r in rows
        ]
        stamps = [r["updated_at"] for r in rows if r["updated_at"] is not None]
        version = _iso(max(stamps)) if stamps else ""
        return rules, version or ""

    def put_project_rules(
        self, rules: list[dict[str, Any]], expected_version: str | None, actor: str = "",
    ) -> None:
        ordered = sorted(
            (
                {"name": r["name"], "pattern": r["pattern"], "position": int(r["position"])}
                for r in rules
            ),
            key=lambda r: r["position"],
        )
        with self._lock:
            with self._conn.transaction():
                # FOR UPDATE locks no row when the table is empty; the advisory lock serializes writers
                self._conn.execute("SELECT pg_advisory_xact_lock(%s)", (_PROJECT_RULES_LOCK,))
                rows = self._conn.execute(
                    f"SELECT name, pattern, position, updated_at FROM {_PROJECT_RULES_TABLE} "
                    "FOR UPDATE",
                ).fetchall()
                stamps = [r["updated_at"] for r in rows if r["updated_at"] is not None]
                stored = _iso(max(stamps)) if stamps else ""
                if expected_version is not None and expected_version != stored:
                    self._conflict("project_rules", "rules", expected_version, stored or "")
                before = [
                    {"name": r["name"], "pattern": r["pattern"], "position": r["position"]}
                    for r in sorted(rows, key=lambda r: r["position"])
                ]
                self._audit("project rules set", before=before, after=ordered, who=actor)
                self._conn.execute(f"DELETE FROM {_PROJECT_RULES_TABLE}")
                for rule in ordered:
                    self._conn.execute(
                        f"INSERT INTO {_PROJECT_RULES_TABLE} "
                        "(name, pattern, position, updated_at) VALUES (%s, %s, %s, now())",
                        (rule["name"], rule["pattern"], rule["position"]),
                    )

    def list_i18n(self) -> dict[str, Any]:
        languages = self._conn.execute(
            f"SELECT code, name, enabled FROM {_I18N_LANGUAGES_TABLE} ORDER BY code"
        ).fetchall()
        strings: dict[str, dict[str, str]] = {}
        for r in self._conn.execute(
            f"SELECT lang, key, text FROM {_I18N_STRINGS_TABLE} ORDER BY lang, key"
        ).fetchall():
            strings.setdefault(r["lang"], {})[r["key"]] = r["text"]
        return {
            "languages": [
                {"code": r["code"], "name": r["name"], "enabled": r["enabled"]}
                for r in languages
            ],
            "strings": strings,
        }

    def put_i18n_string(self, lang: str, key: str, text: str, actor: str = "") -> None:
        with self._lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT text FROM {_I18N_STRINGS_TABLE} "
                    "WHERE lang = %s AND key = %s FOR UPDATE",
                    (lang, key),
                ).fetchone()
                previous = None if row is None else {
                    "lang": lang, "key": key, "text": row["text"],
                }
                if not text:
                    if previous is not None:
                        self._audit("i18n string remove", before=previous, after=None, who=actor)
                        self._conn.execute(
                            f"DELETE FROM {_I18N_STRINGS_TABLE} WHERE lang = %s AND key = %s",
                            (lang, key),
                        )
                    return
                self._audit(
                    "i18n string set",
                    before=previous,
                    after={"lang": lang, "key": key, "text": text},
                    who=actor,
                )
                self._conn.execute(
                    f"INSERT INTO {_I18N_STRINGS_TABLE} (lang, key, text, updated_at, updated_by) "
                    "VALUES (%s, %s, %s, now(), %s) "
                    "ON CONFLICT (lang, key) DO UPDATE SET text = EXCLUDED.text, "
                    "updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by",
                    (lang, key, text, actor or None),
                )

    def add_i18n_language(self, code: str, name: str) -> None:
        with self._lock:
            with self._conn.transaction():
                exists = self._conn.execute(
                    f"SELECT 1 FROM {_I18N_LANGUAGES_TABLE} WHERE code = %s FOR UPDATE",
                    (code,),
                ).fetchone()
                if exists is not None:
                    raise ConflictError(f"i18n language {code!r} already exists")
                after = {"code": code, "name": name, "enabled": True}
                self._audit("i18n language add", before=None, after=after)
                self._conn.execute(
                    f"INSERT INTO {_I18N_LANGUAGES_TABLE} (code, name, enabled) "
                    "VALUES (%s, %s, true)",
                    (code, name),
                )

    def i18n_is_empty(self) -> bool:
        return not self._i18n_has_rows()

    def _i18n_has_rows(self) -> bool:
        row = self._conn.execute(
            f"SELECT EXISTS (SELECT 1 FROM {_I18N_LANGUAGES_TABLE}) "
            f"OR EXISTS (SELECT 1 FROM {_I18N_STRINGS_TABLE}) AS has_rows"
        ).fetchone()
        return bool(row["has_rows"])

    def seed_i18n(self, sets: dict[str, Any]) -> None:
        with self._lock:
            with self._conn.transaction():
                if self._i18n_has_rows():
                    return
                for item in sets.get("languages", []):
                    self._conn.execute(
                        f"INSERT INTO {_I18N_LANGUAGES_TABLE} (code, name, enabled) "
                        "VALUES (%s, %s, true) ON CONFLICT (code) DO NOTHING",
                        (item["code"], item["name"]),
                    )
                for lang, texts in sets.get("strings", {}).items():
                    for key, text in texts.items():
                        if not text:
                            continue
                        self._conn.execute(
                            f"INSERT INTO {_I18N_STRINGS_TABLE} (lang, key, text, updated_at) "
                            "VALUES (%s, %s, %s, now()) ON CONFLICT (lang, key) DO NOTHING",
                            (lang, key, text),
                        )


# ---------------------------------------------------------------------------
def _iso(value: Any) -> str | None:
    """ISO-8601 string of a timestamp column (None stays None)."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _columns_of(view: dict[str, Any]) -> dict[str, Any]:
    """A provider or model view without its version and models: the stored columns."""
    return {k: v for k, v in view.items() if k not in ("version", "models")}


def _provider_dict(row: dict[str, Any], models: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        **{k: row[k] for k in ("name", *_PROVIDER_FIELDS)},
        "version": _iso(row["updated_at"]) or "",
        "models": models,
    }


def _model_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": row["provider"],
        "model": row["model"],
        "probe_result": _read_json(row["probe_result"]),
        "probed_at": _iso(row["probed_at"]),
        **{k: row[k] for k in _PROVIDER_MODEL_FIELDS},
        "version": _iso(row["updated_at"]) or "",
    }


def _model_db(values: dict[str, Any]) -> dict[str, Any]:
    return {
        k: json.dumps(v) if k in _MODEL_JSON_COLUMNS and v is not None else v
        for k, v in values.items()
    }


def _unavailable(reason: str) -> UnavailableBoardStore:
    return UnavailableBoardStore(reason)


def get_board_store(
    dsn: str | None = None,
    driver_check: bool = True,
) -> BoardStore:
    """Factory: pick the Pg store when the DSN is set and connectable;
    otherwise return an unavailable stub whose ``status()`` explains why.

    ``dsn`` defaults to ``DASHBOARD_BOARD_PG_DSN``.  A driver import failure
    (psycopg missing) or a connection failure both yield the unavailable
    stub — the DSN is never printed in the reason.
    """
    if dsn is None:
        dsn = os.environ.get(DSN_ENV, "")
    if not dsn:
        return _unavailable(
            "the database is unavailable: DASHBOARD_BOARD_PG_DSN is not set "
            "(PostgreSQL DSN for the board is not set)"
        )
    try:
        return PgBoardStore(dsn)
    except Exception as exc:  # driver missing (ImportError) or connection failed
        # psycopg's error text may embed the DSN — redact it before reporting.
        message = _redact_dsn(str(exc), dsn)
        return _unavailable(f"the database is unavailable: {message}")


def _redact_dsn(message: str, dsn: str) -> str:
    """Strip the DSN (and any host/port detail) from an error message.

    psycopg's ``OperationalError`` text embeds the connection host, port and
    address, which would expose the connection target.  Redact the whole DSN
    and any host/port/port:addr fragment so only the error class remains."""
    if dsn and dsn in message:
        message = message.replace(dsn, "<redacted>")
    # Redact host/port detail: 'host: X', 'port: Y', 'hostaddr: Z', @host, :port.
    import re
    # Quoted 'host: 'localhost', port: '5432', hostaddr: '::1' form.
    message = re.sub(r"host:\s*'[^']*'", "host: '<redacted>'", message)
    message = re.sub(r"port:\s*'[^']*'", "port: '<redacted>'", message)
    message = re.sub(r"hostaddr:\s*'[^']*'", "hostaddr: '<redacted>'", message)
    # Unquoted 'server at "127.0.0.1", port 5432' / 'at "::1", port 5432' forms.
    message = re.sub(r'"(?:\d{1,3}\.){3}\d{1,3}"', "'<redacted>'", message)
    message = re.sub(r"\"(::[0-9a-fA-F]*)\"", "'<redacted>'", message)
    message = re.sub(r"\bport (\d+)\b", "port <redacted>", message)
    # '@localhost' / ':5432' style fragments.
    for token in ("@localhost", "@127.0.0.1", ":5432", "@ahawr-postgres", "@postgres"):
        if token in message:
            message = message.replace(token, "<redacted>")
    # Drop any remaining quoted IPv4/IPv6 address.
    message = re.sub(r"'(?:\d{1,3}\.){3}\d{1,3}'", "'<redacted>'", message)
    message = re.sub(r"::(?:\d{1,4}|[a-fA-F0-9]){1,4}:", "<redacted>:", message)
    return message
