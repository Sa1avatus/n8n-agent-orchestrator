import sqlite3
from pathlib import Path

from ahawr_retrieval.logstore import RetrievalLog

# retrieval_requests as written before the task correlation keys and query components existed
_OLD_REQUESTS = """
CREATE TABLE retrieval_requests (
    request_id TEXT PRIMARY KEY, ts REAL NOT NULL, profile TEXT NOT NULL, config_id TEXT NOT NULL,
    corpora_json TEXT NOT NULL, trace_json TEXT NOT NULL, query_text TEXT, query_hash TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL, cache_status TEXT NOT NULL, cache_source_request_id TEXT,
    degraded INTEGER NOT NULL, degraded_reasons_json TEXT NOT NULL, n_candidates INTEGER NOT NULL,
    n_filtered INTEGER NOT NULL, n_reranked INTEGER NOT NULL, n_selected INTEGER NOT NULL,
    context_tokens INTEGER NOT NULL, timings_json TEXT NOT NULL, snapshots_json TEXT NOT NULL,
    embedder TEXT NOT NULL, reranker TEXT NOT NULL
);
"""


def test_old_log_is_migrated_before_the_new_indexes(tmp_path: Path) -> None:
    path = tmp_path / "retrieval_logs.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(_OLD_REQUESTS)

    log = RetrievalLog(path)  # used to fail: "no such column: trace_mission_id"
    log.close()

    with sqlite3.connect(path) as conn:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(retrieval_requests)")}
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(retrieval_requests)")}
    assert {"trace_mission_id", "trace_task_id", "query_component", "component_value"} <= columns
    assert {"retrieval_requests_task", "retrieval_requests_component"} <= indexes
