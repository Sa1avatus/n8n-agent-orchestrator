"""Retrieval logging for offline analysis, evaluation and future Learning-to-Rank.

Every /retrieve call writes one request row and one row per candidate, including candidates that
were filtered or not selected, with all ranking features. The log is write-only telemetry kept in
a separate SQLite file: it is never read by the retrieval path and never used to recover AHAWR
state.
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
    config_id TEXT NOT NULL,
    corpora_json TEXT NOT NULL,
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

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def write(self, request: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
        row = {
            "request_id": request["request_id"],
            "ts": request.get("ts", time.time()),
            "profile": request["profile"],
            "config_id": request["config_id"],
            "corpora_json": json.dumps(request["corpora"]),
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
