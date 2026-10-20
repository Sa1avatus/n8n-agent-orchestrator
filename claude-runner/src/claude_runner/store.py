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

# Owner tokens live here while their manager instance is running. A token is
# f"{pid}:{boot_id}" (boot_id is a uuid4 per RunManager). A token is live only
# while in this registry: a manager adds it on startup and removes it on
# shutdown, so a row from a dead manager, or a row from a *different* manager
# in the same process (same pid, different boot id), is never skipped.
_LIVE_OWNER_TOKENS: set[str] = set()

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
    think_policy TEXT NOT NULL DEFAULT '',
    think_from INTEGER NOT NULL DEFAULT 0,
    think_after INTEGER NOT NULL DEFAULT -1,
    think_after_steps INTEGER NOT NULL DEFAULT -1,
    think_task INTEGER NOT NULL DEFAULT 0,
    think_variant TEXT,
    think_mode TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    owner_pid INTEGER
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
        have = {row["name"] for row in self._db.execute("PRAGMA table_info(runs)")}
        for column, ddl in (
            ("think_policy", "TEXT NOT NULL DEFAULT ''"),
            ("think_from", "INTEGER NOT NULL DEFAULT 0"),
            ("think_after", "INTEGER NOT NULL DEFAULT -1"),
            ("think_after_steps", "INTEGER NOT NULL DEFAULT -1"),
            ("think_task", "INTEGER NOT NULL DEFAULT 0"),
            ("think_variant", "TEXT"),
            ("think_mode", "TEXT"),
            ("owner_pid", "INTEGER"),
        ):
            if (
                column not in have
            ):  # databases created before the thinking policy came from hermes_config
                self._db.execute(f"ALTER TABLE runs ADD COLUMN {column} {ddl}")

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

    def session_cost_before(self, claude_session_id: str, run_id: str) -> float | None:
        """Cumulative cost Claude Code reported for this Claude session by the last run that
        finished before ``run_id`` (Claude Code restores the session's cost on --resume)."""
        return self.session_total_before(claude_session_id, run_id, "total_cost_usd")

    def session_total_before(self, claude_session_id: str, run_id: str, key: str) -> float | None:
        """A cumulative session counter from ``details`` (``total_cost_usd``, ``duration_api_ms``)
        as the last run that finished before ``run_id`` reported it."""
        with self._lock:
            rows = self._db.execute(
                "SELECT details FROM runs WHERE claude_session_id = ? AND run_id != ? "
                "AND finished_at IS NOT NULL ORDER BY finished_at DESC, rowid DESC LIMIT 20",
                (claude_session_id, run_id),
            ).fetchall()
        for row in rows:
            total = json.loads(row["details"] or "{}").get(key)
            if isinstance(total, int | float):
                return float(total)
        return None

    def active_run(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT run_id FROM runs WHERE session_id = ? AND status IN (?, ?) "
                "ORDER BY created_at DESC LIMIT 1",
                (session_id, *ACTIVE),
            ).fetchone()
        return self.get_run(row["run_id"]) if row else None

    def supersede_queued(self, session_id: str) -> list[str]:
        """Cancel the queued runs of a session that a later run or an interrupted parent supersedes.

        The parent of a run is the run before it in the session; it supersedes only when it was
        interrupted after the queued run was created.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT run_id, status, created_at, finished_at FROM runs "
                "WHERE session_id = ? ORDER BY created_at, rowid",
                (session_id,),
            ).fetchall()
            superseded = []
            for index, row in enumerate(rows):
                if row["status"] != "queued":
                    continue
                parent = rows[index - 1] if index else None
                newer = index < len(rows) - 1
                parent_interrupted = (
                    parent is not None
                    and parent["status"] == "interrupted"
                    and (parent["finished_at"] or "") > row["created_at"]
                )
                if newer or parent_interrupted:
                    superseded.append(row["run_id"])
            if superseded:
                self._db.executemany(
                    "UPDATE runs SET status = 'cancelled', error_code = 'superseded', "
                    "error_message = ?, finished_at = ? WHERE run_id = ? AND status = 'queued'",
                    [
                        (
                            "Superseded by a newer run or an interrupted parent; never started.",
                            now(),
                            run_id,
                        )
                        for run_id in superseded
                    ],
                )
        return superseded

    def local_compaction_running(self) -> list[str]:
        """Run ids of the running compactions of local Worker sessions."""
        with self._lock:
            rows = self._db.execute(
                "SELECT run_id FROM runs WHERE role = 'worker' "
                "AND provider = 'local' AND status = 'running' AND kind = 'compact'"
            ).fetchall()
        return [r["run_id"] for r in rows]

    def running_local_workers(self) -> list[dict[str, Any]]:
        """Running local Worker runs: run id, session id and the head of the
        prompt (it names the task)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT run_id, session_id, substr(input, 1, 600) AS head, "
                "think_policy, think_from, think_after, think_after_steps, think_task "
                "FROM runs WHERE role = 'worker' "
                "AND provider = 'local' AND status = 'running' "
                "AND kind != 'compact' ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def first_completed_worker_finish(self, session_id: str, exclude_run: str = "") -> str | None:
        """When the first finished (completed) Worker run of this session ended:
        later runs are retries after a review."""
        with self._lock:
            row = self._db.execute(
                "SELECT MIN(finished_at) AS t FROM runs WHERE session_id = ? "
                "AND role = 'worker' AND kind != 'compact' "
                "AND status = 'completed' AND run_id != ?",
                (session_id, exclude_run),
            ).fetchone()
        return row["t"] if row and row["t"] else None

    def last_completed_worker_finish(self, session_id: str, exclude_run: str = "") -> str | None:
        """When the latest finished (completed) Worker run of this session ended:
        the start of the current attempt (None = the first attempt)."""
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(finished_at) AS t FROM runs WHERE session_id = ? "
                "AND role = 'worker' AND kind != 'compact' "
                "AND status = 'completed' AND run_id != ?",
                (session_id, exclude_run),
            ).fetchone()
        return row["t"] if row and row["t"] else None

    def session_seconds_since(self, session_id: str, since: str) -> float:
        """Seconds the runs of a session spent running since ``since`` (a
        finished run from start to finish, a running one to now)."""
        moment = datetime.now(UTC)
        floor = datetime.fromisoformat(since)
        total = 0.0
        with self._lock:
            rows = self._db.execute(
                "SELECT started_at, finished_at FROM runs WHERE session_id = ? "
                "AND started_at IS NOT NULL AND kind != 'compact'",
                (session_id,),
            ).fetchall()
        for row in rows:
            start = max(datetime.fromisoformat(row["started_at"]), floor)
            end = datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else moment
            total += max(0.0, (end - start).total_seconds())
        return total

    def session_run_ids(self, session_id: str, since: str | None = None) -> list[str]:
        """Runs of a Worker session (compactions left out); with ``since`` only
        those that started at or after it."""
        sql = (
            "SELECT run_id FROM runs WHERE session_id = ? "
            "AND started_at IS NOT NULL AND kind != 'compact'"
        )
        args: list[Any] = [session_id]
        if since:
            sql += " AND started_at >= ?"
            args.append(since)
        with self._lock:
            return [row["run_id"] for row in self._db.execute(sql, args).fetchall()]

    def local_worker_running(self) -> bool:
        """A local Worker run is running right now."""
        with self._lock:
            return (
                self._db.execute(
                    "SELECT 1 FROM runs WHERE role = 'worker' "
                    "AND provider = 'local' AND status = 'running' LIMIT 1"
                ).fetchone()
                is not None
            )

    def local_worker_sessions_over(self, limit_seconds: int) -> list[dict[str, Any]]:
        """Sessions of a running local Worker run whose runs add up to more than ``limit_seconds``.

        One entry per such session: the running run's id and the summed seconds of all its runs
        (a finished run counts from start to finish, a running one up to now). 0 = never."""
        if limit_seconds <= 0:
            return []
        with self._lock:
            active = self._db.execute(
                "SELECT run_id, session_id FROM runs WHERE role = 'worker' AND provider = 'local' "
                "AND status = 'running'"
            ).fetchall()
            out = []
            moment = datetime.now(UTC)
            for item in active:
                rows = self._db.execute(
                    "SELECT started_at, finished_at FROM runs WHERE session_id = ? "
                    "AND started_at IS NOT NULL",
                    (item["session_id"],),
                ).fetchall()
                seconds = 0.0
                for row in rows:
                    start = datetime.fromisoformat(row["started_at"])
                    end = (
                        datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else moment
                    )
                    seconds += max(0.0, (end - start).total_seconds())
                if seconds > limit_seconds:
                    out.append(
                        {
                            "run_id": item["run_id"],
                            "session_id": item["session_id"],
                            "seconds": int(seconds),
                        }
                    )
        return out

    def list_runs(
        self, limit: int = 50, session_id: str = "", role: str = "", status: str = ""
    ) -> list[dict[str, Any]]:
        """Newest first, optionally filtered (for the dashboard)."""
        where, params = [], []
        for column, value in (("session_id", session_id), ("role", role), ("status", status)):
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        sql = "SELECT run_id FROM runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        with self._lock:
            ids = [row["run_id"] for row in self._db.execute(sql, (*params, limit))]
        return [run for run in map(self.get_run, ids) if run]

    def _owner_alive(self, owner_token: str | None) -> bool:
        """True when the owner token belongs to a live manager instance (registry)."""
        if not owner_token:
            return False
        return owner_token in _LIVE_OWNER_TOKENS

    def interrupt_active_runs(self, reason: str) -> int:
        """Mark runs a previous runner process left behind as interrupted.
        A row is skipped only when its owner token is registered as live (a
        manager added it on startup and has not removed it yet). A token from a
        dead manager, or a token from a *different* manager in the same process
        (different boot id, even the same pid), is not live and is interrupted."""
        with self._lock:
            rows = self._db.execute(
                "SELECT run_id, owner_pid FROM runs WHERE status IN (?, ?)",
                ACTIVE,
            ).fetchall()
            orphaned = [row["run_id"] for row in rows if not self._owner_alive(row["owner_pid"])]
            if not orphaned:
                return 0
            cursor = self._db.executemany(
                "UPDATE runs SET status = 'interrupted', error_code = 'run_not_found', "
                "error_message = ?, finished_at = ? WHERE run_id = ? AND status IN (?, ?)",
                [(reason, now(), run_id, *ACTIVE) for run_id in orphaned],
            )
        return cursor.rowcount

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) AS n FROM runs GROUP BY status")
            return {row["status"]: row["n"] for row in rows}
