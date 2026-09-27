"""SQLite persistence for runs and sessions (on the runner's /data volume).

A *runner session* is the opaque ``session_id`` AHAWR stores in its Data Tables. It maps to a
Claude Code session (the CLI transcript under ``CLAUDE_CONFIG_DIR``). Usually both ids are the
same UUID; they differ only when AHAWR asks to continue a session Claude Code does not know
(e.g. an id left over from Hermes), in which case a fresh Claude Code session is bound to it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ACTIVE = ("queued", "running")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    claude_session_id TEXT NOT NULL,
    cwd TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_run_id TEXT NOT NULL DEFAULT '',
    context_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'run',
    session_id TEXT NOT NULL,
    claude_session_id TEXT NOT NULL,
    session_created INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    cwd TEXT NOT NULL,
    input TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    output TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    http_code INTEGER,
    cost_usd REAL,
    num_turns INTEGER,
    context_tokens INTEGER,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS runs_by_session ON runs(session_id, status);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=30000")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # sessions -----------------------------------------------------------------------------
    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def bind_session(self, session_id: str, claude_session_id: str, cwd: str, role: str) -> None:
        stamp = now()
        with self._lock:
            self._db.execute(
                """INSERT INTO sessions(session_id, claude_session_id, cwd, role, created_at,
                                        updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET claude_session_id = excluded.
                   claude_session_id, cwd = excluded.cwd, updated_at = excluded.updated_at,
                   context_tokens = CASE WHEN sessions.claude_session_id = excluded.
                   claude_session_id THEN sessions.context_tokens ELSE 0 END""",
                (session_id, claude_session_id, cwd, role, stamp, stamp),
            )

    def touch_session(self, session_id: str, run_id: str, context_tokens: int | None) -> None:
        with self._lock:
            self._db.execute(
                """UPDATE sessions SET updated_at = ?, last_run_id = ?,
                   context_tokens = COALESCE(?, context_tokens) WHERE session_id = ?""",
                (now(), run_id, context_tokens, session_id),
            )

    # runs ---------------------------------------------------------------------------------
    def create_run(self, **fields: Any) -> None:
        fields.setdefault("created_at", now())
        fields.setdefault("status", "queued")
        columns = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self._lock:
            self._db.execute(
                f"INSERT INTO runs({columns}) VALUES ({marks})", tuple(fields.values())
            )

    def update_run(self, run_id: str, **fields: Any) -> None:
        if "details" in fields and not isinstance(fields["details"], str):
            fields["details"] = json.dumps(fields["details"], ensure_ascii=False)
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._lock:
            self._db.execute(
                f"UPDATE runs SET {assignments} WHERE run_id = ?", (*fields.values(), run_id)
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        run = dict(row)
        run["details"] = json.loads(run.get("details") or "{}")
        return run

    def active_run(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT run_id FROM runs WHERE session_id = ? AND status IN (?, ?) "
                "ORDER BY created_at DESC LIMIT 1",
                (session_id, *ACTIVE),
            ).fetchone()
        return self.get_run(row["run_id"]) if row else None

    def interrupt_active_runs(self, reason: str) -> int:
        """Runs a previous runner process left behind can never finish: mark them."""
        with self._lock:
            cursor = self._db.execute(
                "UPDATE runs SET status = 'interrupted', error_code = 'run_not_found', "
                "error_message = ?, finished_at = ? WHERE status IN (?, ?)",
                (reason, now(), *ACTIVE),
            )
        return cursor.rowcount

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) AS n FROM runs GROUP BY status")
            return {row["status"]: row["n"] for row in rows}
