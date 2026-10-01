"""Retrieval logging for offline analysis, evaluation and future Learning-to-Rank.

Every /retrieve call writes one request row and one row per candidate, including candidates that
were filtered or not selected, with all ranking features. The log is telemetry kept in a separate
SQLite file and never used to recover AHAWR state. The retrieval path reads it for the automatic
Reviewer boost (see service.py): ``last_request`` looks up the most recent Worker request with the
same correlation keys (the task ids, from the request's task falling back to its trace), and when
that finds nothing ``last_worker_request`` falls back to the task title/objective hash. The chosen
request's timestamp is the cutoff for the change journal. Both reads are indexed lookups — they
never scan the log — and the log remains write-only for every other consumer (eval harness,
offline analysis).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS retrieval_requests (
    request_id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    profile TEXT NOT NULL,
    trace_mission_id TEXT NOT NULL DEFAULT '',
    trace_task_id TEXT NOT NULL DEFAULT '',
    config_id TEXT NOT NULL,
    corpora_json TEXT NOT NULL,
    query_component TEXT NOT NULL DEFAULT '',
    component_value TEXT NOT NULL DEFAULT '',
    trace_json TEXT NOT NULL,
    query_text TEXT,
    query_hash TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL,
    cache_status TEXT NOT NULL,
    cache_source_request_id TEXT,
    degraded INTEGER NOT NULL,
    degraded_reasons_json TEXT NOT NULL,
    n_candidates INTEGER NOT NULL,
    n_filtered INTEGER NOT NULL,
    n_reranked INTEGER NOT NULL,
    n_selected INTEGER NOT NULL,
    context_tokens INTEGER NOT NULL,
    timings_json TEXT NOT NULL,
    snapshots_json TEXT NOT NULL,
    embedder TEXT NOT NULL,
    reranker TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS retrieval_requests_ts ON retrieval_requests(ts);
CREATE TABLE IF NOT EXISTS retrieval_candidates (
    request_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    corpus_id TEXT,
    path TEXT,
    source_type TEXT,
    symbol TEXT,
    section TEXT,
    content_hash TEXT,
    token_count INTEGER,
    chunk_age_seconds REAL,
    lexical_score REAL,
    lexical_rank INTEGER,
    vector_score REAL,
    vector_rank INTEGER,
    symbol_score REAL,
    symbol_rank INTEGER,
    fused_score REAL,
    fused_rank INTEGER,
    reranker_score REAL,
    reranker_raw REAL,
    reranker_rank INTEGER,
    exact_symbol REAL,
    path_mentioned REAL,
    scope_match REAL,
    test_file REAL,
    source_prior REAL,
    deterministic_score REAL,
    final_score REAL,
    final_rank INTEGER,
    freshness TEXT,
    filtered_reason TEXT,
    selected INTEGER NOT NULL,
    selection_reason TEXT,
    PRIMARY KEY (request_id, chunk_id)
);
"""

CANDIDATE_FIELDS = (
    "chunk_id",
    "corpus_id",
    "path",
    "source_type",
    "symbol",
    "section",
    "content_hash",
    "token_count",
    "chunk_age_seconds",
    "lexical_score",
    "lexical_rank",
    "vector_score",
    "vector_rank",
    "symbol_score",
    "symbol_rank",
    "fused_score",
    "fused_rank",
    "reranker_score",
    "reranker_raw",
    "reranker_rank",
    "exact_symbol",
    "path_mentioned",
    "scope_match",
    "test_file",
    "source_prior",
    "deterministic_score",
    "final_score",
    "final_rank",
    "freshness",
    "filtered_reason",
    "selected",
    "selection_reason",
)


# Indexes on columns that older logs only get by the migration in RetrievalLog.__init__,
# so they are created after it.
_INDEXES = """
-- Correlation keys of the request's task, in canonical order, for the automatic
-- Reviewer boost's last-request lookup (see last_request below).
CREATE INDEX IF NOT EXISTS retrieval_requests_task
    ON retrieval_requests(trace_mission_id, trace_task_id, profile, ts);
-- Fingerprint-component lookup for the Reviewer boost's fallback key (see
-- last_worker_request below); the component key is the name, the value its exact hash.
CREATE INDEX IF NOT EXISTS retrieval_requests_component
    ON retrieval_requests(profile, query_component, component_value, ts);
"""


class RetrievalLog:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            # Migration: v2 logs predate the denormalized task correlation keys.
            columns = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(retrieval_requests)")
            }
            if "trace_mission_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE retrieval_requests "
                    "ADD COLUMN trace_mission_id TEXT NOT NULL DEFAULT ''"
                )
                self._conn.execute(
                    "ALTER TABLE retrieval_requests "
                    "ADD COLUMN trace_task_id TEXT NOT NULL DEFAULT ''"
                )
            if "query_component" not in columns:
                self._conn.execute(
                    "ALTER TABLE retrieval_requests "
                    "ADD COLUMN query_component TEXT NOT NULL DEFAULT ''"
                )
                self._conn.execute(
                    "ALTER TABLE retrieval_requests "
                    "ADD COLUMN component_value TEXT NOT NULL DEFAULT ''"
                )
            self._conn.executescript(_INDEXES)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def write(self, request: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
        trace = request.get("trace") or {}
        row = {
            "request_id": request["request_id"],
            "ts": request.get("ts", time.time()),
            "profile": request["profile"],
            "trace_mission_id": request.get("task_mission_id") or trace.get("mission_id") or "",
            "trace_task_id": request.get("task_task_id") or trace.get("task_id") or "",
            "config_id": request["config_id"],
            "corpora_json": request.get("corpora_json") or json.dumps(request["corpora"]),
            "query_component": request.get("query_component") or "",
            "component_value": request.get("component_value") or "",
            "trace_json": json.dumps(request.get("trace") or {}, sort_keys=True),
            "query_text": request.get("query_text"),
            "query_hash": request["query_hash"],
            "fingerprint_json": json.dumps(request.get("fingerprint") or {}, sort_keys=True),
            "cache_status": request["cache_status"],
            "cache_source_request_id": request.get("cache_source_request_id"),
            "degraded": int(bool(request.get("degraded"))),
            "degraded_reasons_json": json.dumps(request.get("degraded_reasons") or []),
            "n_candidates": request.get("n_candidates", 0),
            "n_filtered": request.get("n_filtered", 0),
            "n_reranked": request.get("n_reranked", 0),
            "n_selected": request.get("n_selected", 0),
            "context_tokens": request.get("context_tokens", 0),
            "timings_json": json.dumps(request.get("timings_ms") or {}, sort_keys=True),
            "snapshots_json": json.dumps(request.get("snapshots") or {}, sort_keys=True),
            "embedder": request.get("embedder", ""),
            "reranker": request.get("reranker", ""),
        }
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    f"INSERT OR REPLACE INTO retrieval_requests({','.join(row)}) "
                    f"VALUES ({','.join('?' * len(row))})",
                    tuple(row.values()),
                )
                self._conn.executemany(
                    f"INSERT OR REPLACE INTO retrieval_candidates(request_id,"
                    f"{','.join(CANDIDATE_FIELDS)}) VALUES "
                    f"({','.join('?' * (len(CANDIDATE_FIELDS) + 1))})",
                    [
                        (request["request_id"], *(_sql(c.get(f)) for f in CANDIDATE_FIELDS))
                        for c in candidates
                    ],
                )
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def last_request(
        self, profile: str, mission_id: str, task_id: str, before_ts: float
    ) -> dict[str, Any] | None:
        """The most recent retrieval request with the given profile and correlation keys.

        Correlation keys are the task identifiers, taken from the request's ``task``
        falling back to its ``trace`` (``mission_id`` / ``task_id``), denormalized into
        the indexed columns ``trace_mission_id`` / ``trace_task_id`` at write time (see
        ``write``); a request matches when both equal the given keys. ``before_ts``
        excludes the caller's own row (pass the caller's timestamp). The lookup is an
        indexed point query on ``(trace_mission_id, trace_task_id)`` followed by a ts
        comparison — it never scans the log, so the overhead of the automatic Reviewer
        boost stays negligible even for long logs. Returns the full request row, or
        ``None`` when no earlier request for the same task exists.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM retrieval_requests "
                "WHERE trace_mission_id = ? AND trace_task_id = ? AND profile = ? "
                "AND ts < ? ORDER BY ts DESC LIMIT 1",
                (mission_id, task_id, profile, before_ts),
            ).fetchone()
        return dict(row) if row is not None else None

    def last_worker_request(self, key: str, value: str, before_ts: float) -> dict[str, Any] | None:
        """Most recent worker request carrying the given fingerprint component key/value.

        Fallback correlation for the automatic Reviewer boost: it runs whenever the
        correlation-key lookup finds no matching worker request — either because no
        mission/task ids are available, or because those ids do not match any logged
        worker request. It is an indexed range query on
        (profile, query_component, component_value, ts), so it never scans the log and
        the hash comparison stays in-process. ``component_value`` is
        ``short_hash(normalize_whitespace(title), normalize_whitespace(objective))`` —
        the hash of the task component stored as ``component_value`` by ``write`` (with
        ``query_component == "task"``) — which is stable across worker and reviewer
        requests for the same task text. Returns the full request row, or ``None``
        when no earlier worker request matched.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM retrieval_requests "
                "WHERE profile = 'worker' AND query_component = ? AND component_value = ? "
                "AND ts < ? ORDER BY ts DESC LIMIT 1",
                (key, value, before_ts),
            ).fetchone()
        return dict(row) if row is not None else None

    def requests(self, since: float | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM retrieval_requests WHERE ts >= ? ORDER BY ts",
                (since or 0,),
            ).fetchall()
        return [dict(r) for r in rows]

    def iter_feature_rows(self, since: float | None = None) -> Iterator[dict[str, Any]]:
        """Join request context with candidate features: one row per (request, candidate)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT r.ts, r.profile, r.config_id, r.trace_json, r.query_hash, r.cache_status, "
                "c.* FROM retrieval_candidates c JOIN retrieval_requests r "
                "ON r.request_id = c.request_id WHERE r.ts >= ? ORDER BY r.ts, c.final_rank",
                (since or 0,),
            ).fetchall()
        for row in rows:
            data = dict(row)
            data["trace"] = json.loads(data.pop("trace_json") or "{}")
            data["selected"] = bool(data["selected"])
            yield data


def _sql(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value
