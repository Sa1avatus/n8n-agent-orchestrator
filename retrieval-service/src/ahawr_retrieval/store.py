"""SQLite-backed index store: corpus manifests, chunks, FTS5 lexical index, symbols, embeddings,
change log and retrieval cache.

The store holds *retrieval* data only. It never contains AHAWR execution state
(run/session identifiers, task position, review status); see ARCHITECTURE_CONTRACT.md §5.1.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA_VERSION = 4

FTS_TOKENIZER = "porter unicode61 remove_diacritics 2 tokenchars '_'"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS corpora (
    corpus_id TEXT PRIMARY KEY,
    root TEXT,
    code_generation INTEGER NOT NULL DEFAULT 0,
    docs_generation INTEGER NOT NULL DEFAULT 0,
    code_snapshot TEXT,
    docs_snapshot TEXT,
    git_head TEXT,
    config_json TEXT NOT NULL DEFAULT '{{}}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_sync_at REAL,
    stale INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS files (
    corpus_id TEXT NOT NULL,
    path TEXT NOT NULL,
    source_type TEXT NOT NULL,
    origin TEXT NOT NULL,
    language TEXT,
    file_hash TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    status_reason TEXT,
    snapshot_id TEXT,
    title TEXT,
    source_url TEXT,
    valid_until REAL,
    indexed_at REAL NOT NULL,
    PRIMARY KEY (corpus_id, path)
);
-- Change journal: files re-indexed since a timestamp, per corpus (automatic Reviewer boost).
CREATE INDEX IF NOT EXISTS files_corpus_indexed
    ON files(corpus_id, indexed_at, path);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id TEXT NOT NULL UNIQUE,
    corpus_id TEXT NOT NULL,
    path TEXT NOT NULL,
    source_type TEXT NOT NULL,
    anchor TEXT NOT NULL,
    symbol TEXT,
    symbol_kind TEXT,
    section TEXT,
    language TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    version TEXT NOT NULL,
    snapshot_id TEXT,
    token_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    status_reason TEXT,
    generation INTEGER NOT NULL,
    indexed_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_corpus_status ON chunks(corpus_id, status, source_type);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(corpus_id, path);
CREATE INDEX IF NOT EXISTS chunks_hash ON chunks(content_hash);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    body, symbols, path, tokenize="{FTS_TOKENIZER}"
);
CREATE TABLE IF NOT EXISTS symbols (
    corpus_id TEXT NOT NULL,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    name_lower TEXT NOT NULL,
    qualname TEXT NOT NULL,
    kind TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    chunk_id TEXT
);
CREATE INDEX IF NOT EXISTS symbols_name ON symbols(corpus_id, name_lower);
CREATE INDEX IF NOT EXISTS symbols_path ON symbols(corpus_id, path);
CREATE TABLE IF NOT EXISTS embeddings (
    model_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (model_id, content_hash)
);
CREATE TABLE IF NOT EXISTS changes (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    corpus_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    generation INTEGER NOT NULL,
    chunk_id TEXT NOT NULL,
    path TEXT NOT NULL,
    change TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS changes_corpus ON changes(corpus_id, source_type, generation);
CREATE TABLE IF NOT EXISTS retrieval_cache (
    cache_key TEXT PRIMARY KEY,
    scope_key TEXT NOT NULL,
    state_json TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_hit_at REAL,
    hits INTEGER NOT NULL DEFAULT 0,
    changed_paths_json TEXT
);
CREATE INDEX IF NOT EXISTS retrieval_cache_scope ON retrieval_cache(scope_key, created_at);
CREATE TABLE IF NOT EXISTS rag_mirror (
    chunk_id TEXT PRIMARY KEY,
    corpus_id TEXT NOT NULL,
    remote_document_id TEXT,
    remote_version INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    pushed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS rag_mirror_corpus ON rag_mirror(corpus_id);
CREATE TABLE IF NOT EXISTS rag_mirror_state (
    corpus_id TEXT PRIMARY KEY,
    cursor_seq INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_attempt_at REAL,
    last_success_at REAL
);
"""

ACTIVE = "active"
DELETED = "deleted"
INVALIDATED = "invalidated"


@dataclass
class CorpusRecord:
    corpus_id: str
    root: str | None
    code_generation: int = 0
    docs_generation: int = 0
    code_snapshot: str | None = None
    docs_snapshot: str | None = None
    git_head: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    last_sync_at: float | None = None
    stale: bool = False

    def generation(self, source_type: str) -> int:
        return self.code_generation if source_type == "code" else self.docs_generation

    def snapshot(self, source_type: str) -> str | None:
        return self.code_snapshot if source_type == "code" else self.docs_snapshot


@dataclass
class FileRecord:
    corpus_id: str
    path: str
    source_type: str
    origin: str
    language: str | None
    file_hash: str
    size: int
    mtime_ns: int
    version: str
    status: str
    status_reason: str | None
    snapshot_id: str | None
    title: str | None
    source_url: str | None
    valid_until: float | None
    indexed_at: float


@dataclass
class ChunkRecord:
    id: int
    chunk_id: str
    corpus_id: str
    path: str
    source_type: str
    anchor: str
    symbol: str | None
    symbol_kind: str | None
    section: str | None
    language: str | None
    start_line: int
    end_line: int
    content: str
    content_hash: str
    file_hash: str
    version: str
    snapshot_id: str | None
    token_count: int
    status: str
    status_reason: str | None
    generation: int
    indexed_at: float
    updated_at: float


@dataclass(frozen=True)
class ChangeRecord:
    seq: int
    corpus_id: str
    source_type: str
    generation: int
    chunk_id: str
    path: str
    change: str


_CHUNK_COLUMNS = (
    "id, chunk_id, corpus_id, path, source_type, anchor, symbol, symbol_kind, section, language, "
    "start_line, end_line, content, content_hash, file_hash, version, snapshot_id, token_count, "
    "status, status_reason, generation, indexed_at, updated_at"
)
_FILE_COLUMNS = (
    "corpus_id, path, source_type, origin, language, file_hash, size, mtime_ns, version, status, "
    "status_reason, snapshot_id, title, source_url, valid_until, indexed_at"
)


def _placeholders(count: int) -> str:
    return ",".join("?" * count)


class Store:
    """Thread-safe single-connection SQLite store (WAL). All access goes through ``lock``."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            # Several processes may share the index (n8n Execute Command runs one per call).
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            # Migration: v1 caches predate the changed_paths dimension; add the column.
            columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(retrieval_cache)")}
            if "changed_paths_json" not in columns:
                self._conn.execute("ALTER TABLE retrieval_cache ADD COLUMN changed_paths_json TEXT")
            # v3 -> v4: stale flag marks a corpus whose root no longer exists.
            corpus_columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(corpora)")}
            if "stale" not in corpus_columns:
                self._conn.execute(
                    "ALTER TABLE corpora ADD COLUMN stale INTEGER NOT NULL DEFAULT 0"
                )
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def get_meta(self, key: str) -> str | None:
        rows = self._query("SELECT value FROM meta WHERE key = ?", (key,))
        return str(rows[0]["value"]) if rows else None

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    def close(self) -> None:
        with self.lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return list(self._conn.execute(sql, params).fetchall())

    # ---------------------------------------------------------------- corpora

    def get_corpus(self, corpus_id: str) -> CorpusRecord | None:
        rows = self._query("SELECT * FROM corpora WHERE corpus_id = ?", (corpus_id,))
        return self._corpus(rows[0]) if rows else None

    def list_corpora(self) -> list[CorpusRecord]:
        return [self._corpus(r) for r in self._query("SELECT * FROM corpora ORDER BY corpus_id")]

    def ensure_corpus(
        self, corpus_id: str, root: str | None, config: dict[str, Any] | None = None
    ) -> CorpusRecord:
        now = time.time()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM corpora WHERE corpus_id = ?", (corpus_id,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO corpora"
                    "(corpus_id, root, config_json, created_at, updated_at, stale) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (corpus_id, root, json.dumps(config or {}, sort_keys=True), now, now),
                )
            else:
                updates: dict[str, Any] = {"updated_at": now}
                if root is not None and root != existing["root"]:
                    updates["root"] = root
                if config:
                    merged = {**json.loads(existing["config_json"]), **config}
                    updates["config_json"] = json.dumps(merged, sort_keys=True)
                sets = ", ".join(f"{key} = ?" for key in updates)
                conn.execute(
                    f"UPDATE corpora SET {sets} WHERE corpus_id = ?", (*updates.values(), corpus_id)
                )
        corpus = self.get_corpus(corpus_id)
        assert corpus is not None
        return corpus

    def update_corpus_state(self, corpus_id: str, **fields: Any) -> None:
        allowed = {
            "code_generation",
            "docs_generation",
            "code_snapshot",
            "docs_snapshot",
            "git_head",
            "last_sync_at",
            "stale",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown corpus fields: {sorted(unknown)}")
        if not fields:
            return
        fields["updated_at"] = time.time()
        sets = ", ".join(f"{key} = ?" for key in fields)
        with self.transaction() as conn:
            conn.execute(
                f"UPDATE corpora SET {sets} WHERE corpus_id = ?", (*fields.values(), corpus_id)
            )

    @staticmethod
    def _corpus(row: sqlite3.Row) -> CorpusRecord:
        return CorpusRecord(
            corpus_id=row["corpus_id"],
            root=row["root"],
            code_generation=row["code_generation"],
            docs_generation=row["docs_generation"],
            code_snapshot=row["code_snapshot"],
            docs_snapshot=row["docs_snapshot"],
            git_head=row["git_head"],
            config=json.loads(row["config_json"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_sync_at=row["last_sync_at"],
            stale=bool(row["stale"]),
        )

    def mark_stale(self, corpus_id: str, stale: bool) -> None:
        """Persist the corpus's stale flag (root no longer exists) without touching state."""
        with self.transaction() as conn:
            conn.execute(
                "UPDATE corpora SET stale = ?, updated_at = ? WHERE corpus_id = ?",
                (1 if stale else 0, time.time(), corpus_id),
            )

    def delete_all_files(self, corpus_id: str) -> int:
        """Remove every file record of a corpus (e.g. when its root no longer exists)."""
        with self.transaction() as conn:
            return int(conn.execute("DELETE FROM files WHERE corpus_id = ?", (corpus_id,)).rowcount)

    # ------------------------------------------------------------------ files

    def files(self, corpus_id: str, origin: str | None = None) -> dict[str, FileRecord]:
        sql = f"SELECT {_FILE_COLUMNS} FROM files WHERE corpus_id = ?"
        params: list[Any] = [corpus_id]
        if origin:
            sql += " AND origin = ?"
            params.append(origin)
        return {r["path"]: FileRecord(**dict(r)) for r in self._query(sql, params)}

    def get_files(self, corpus_id: str, paths: Iterable[str]) -> dict[str, FileRecord]:
        wanted = list(dict.fromkeys(paths))
        result: dict[str, FileRecord] = {}
        for offset in range(0, len(wanted), 500):
            batch = wanted[offset : offset + 500]
            rows = self._query(
                f"SELECT {_FILE_COLUMNS} FROM files WHERE corpus_id = ? "
                f"AND path IN ({_placeholders(len(batch))})",
                (corpus_id, *batch),
            )
            result.update({r["path"]: FileRecord(**dict(r)) for r in rows})
        return result

    @staticmethod
    def upsert_file(conn: sqlite3.Connection, record: FileRecord) -> None:
        values = [getattr(record, c.strip()) for c in _FILE_COLUMNS.split(",")]
        conn.execute(
            f"INSERT OR REPLACE INTO files({_FILE_COLUMNS}) VALUES ({_placeholders(len(values))})",
            values,
        )

    # ----------------------------------------------------------------- chunks

    @staticmethod
    def _chunk(row: sqlite3.Row) -> ChunkRecord:
        return ChunkRecord(**dict(row))

    def chunks_for_path(self, corpus_id: str, path: str) -> dict[str, ChunkRecord]:
        rows = self._query(
            f"SELECT {_CHUNK_COLUMNS} FROM chunks WHERE corpus_id = ? AND path = ?",
            (corpus_id, path),
        )
        return {r["chunk_id"]: self._chunk(r) for r in rows}

    def get_chunks(self, chunk_ids: Iterable[str]) -> dict[str, ChunkRecord]:
        wanted = list(dict.fromkeys(chunk_ids))
        result: dict[str, ChunkRecord] = {}
        for offset in range(0, len(wanted), 500):
            batch = wanted[offset : offset + 500]
            rows = self._query(
                f"SELECT {_CHUNK_COLUMNS} FROM chunks WHERE chunk_id IN "
                f"({_placeholders(len(batch))})",
                batch,
            )
            result.update({r["chunk_id"]: self._chunk(r) for r in rows})
        return result

    def active_chunks_for_paths(
        self, corpus_id: str, paths: Sequence[str], limit_per_path: int
    ) -> list[ChunkRecord]:
        result: list[ChunkRecord] = []
        for path in paths:
            rows = self._query(
                f"SELECT {_CHUNK_COLUMNS} FROM chunks WHERE corpus_id = ? AND path = ? "
                "AND status = 'active' ORDER BY start_line LIMIT ?",
                (corpus_id, path, limit_per_path),
            )
            result.extend(self._chunk(r) for r in rows)
        return result

    def file_line_count(self, corpus_id: str, path: str) -> int | None:
        """Whole-file line count: the maximum ``end_line`` across the file's
        active chunks. ``None`` when the file has no active chunks."""
        row = self._query(
            "SELECT MAX(end_line) AS max_line "
            "FROM chunks WHERE corpus_id = ? AND path = ? AND status = 'active'",
            (corpus_id, path),
        )
        value = row[0]["max_line"] if row else None
        return int(value) if value is not None else None

    @staticmethod
    def insert_chunk(
        conn: sqlite3.Connection, record: ChunkRecord, fts: tuple[str, str, str]
    ) -> int:
        values = [getattr(record, c.strip()) for c in _CHUNK_COLUMNS.split(",")][1:]
        cursor = conn.execute(
            f"INSERT INTO chunks({_CHUNK_COLUMNS.split(', ', 1)[1]}) "
            f"VALUES ({_placeholders(len(values))})",
            values,
        )
        rowid = int(cursor.lastrowid or 0)
        if record.status == ACTIVE:
            conn.execute(
                "INSERT INTO chunks_fts(rowid, body, symbols, path) VALUES (?, ?, ?, ?)",
                (rowid, *fts),
            )
        return rowid

    @staticmethod
    def replace_chunk(
        conn: sqlite3.Connection, record: ChunkRecord, fts: tuple[str, str, str] | None
    ) -> None:
        """Rewrite a chunk row in place (same rowid); refresh its FTS row when ``fts`` given."""
        columns = [c.strip() for c in _CHUNK_COLUMNS.split(",")][2:]
        sets = ", ".join(f"{c} = ?" for c in columns)
        conn.execute(
            f"UPDATE chunks SET {sets} WHERE id = ?",
            (*(getattr(record, c) for c in columns), record.id),
        )
        if fts is not None:
            conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (record.id,))
            if record.status == ACTIVE:
                conn.execute(
                    "INSERT INTO chunks_fts(rowid, body, symbols, path) VALUES (?, ?, ?, ?)",
                    (record.id, *fts),
                )

    @staticmethod
    def deactivate_chunk(
        conn: sqlite3.Connection, record: ChunkRecord, status: str, reason: str, generation: int
    ) -> None:
        conn.execute(
            "UPDATE chunks SET status = ?, status_reason = ?, generation = ?, updated_at = ? "
            "WHERE id = ?",
            (status, reason, generation, time.time(), record.id),
        )
        conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (record.id,))

    @staticmethod
    def record_change(
        conn: sqlite3.Connection,
        corpus_id: str,
        source_type: str,
        generation: int,
        chunk_id: str,
        path: str,
        change: str,
    ) -> None:
        conn.execute(
            "INSERT INTO changes(corpus_id, source_type, generation, chunk_id, path, change, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (corpus_id, source_type, generation, chunk_id, path, change, time.time()),
        )

    @staticmethod
    def replace_symbols(
        conn: sqlite3.Connection,
        corpus_id: str,
        path: str,
        symbols: Iterable[tuple[str, str, str, int, int, str | None]],
    ) -> None:
        conn.execute("DELETE FROM symbols WHERE corpus_id = ? AND path = ?", (corpus_id, path))
        conn.executemany(
            "INSERT INTO symbols(corpus_id, path, name, name_lower, qualname, kind, start_line, "
            "end_line, chunk_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (corpus_id, path, name, name.lower(), qualname, kind, start, end, chunk_id)
                for name, qualname, kind, start, end, chunk_id in symbols
            ],
        )

    def changes_since(
        self,
        seq: int = 0,
        corpus_id: str | None = None,
        source_type: str | None = None,
        min_generation: int | None = None,
    ) -> list[ChangeRecord]:
        sql = "SELECT seq, corpus_id, source_type, generation, chunk_id, path, change FROM changes"
        clauses = ["seq > ?"]
        params: list[Any] = [seq]
        if corpus_id:
            clauses.append("corpus_id = ?")
            params.append(corpus_id)
        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)
        if min_generation is not None:
            clauses.append("generation > ?")
            params.append(min_generation)
        sql += " WHERE " + " AND ".join(clauses) + " ORDER BY seq"
        return [ChangeRecord(**dict(r)) for r in self._query(sql, params)]

    def max_change_seq(self) -> int:
        rows = self._query("SELECT COALESCE(MAX(seq), 0) AS seq FROM changes")
        return int(rows[0]["seq"])

    def files_indexed_since(self, since_ts: float, corpora: Sequence[str]) -> list[tuple[str, str]]:
        """Corpus files re-indexed after ``since_ts`` (the sync/index change journal).

        ``files.indexed_at`` is refreshed on every re-index, so the set of files changed
        since the given timestamp is exactly ``indexed_at > since_ts``. The query is
        indexed on ``(corpus_id, indexed_at, path)`` (files_corpus_indexed) and therefore
        reads only the rows after the cutoff — cheap even for large corpora. Returns
        ``(corpus_id, path)`` pairs, ordered by corpus then path.
        """
        if not corpora:
            return []
        rows = self._query(
            f"SELECT corpus_id, path FROM files WHERE corpus_id IN "
            f"({_placeholders(len(corpora))}) AND indexed_at > ? "
            "ORDER BY corpus_id, path",
            (*corpora, since_ts),
        )
        return [(r["corpus_id"], r["path"]) for r in rows]

    # ------------------------------------------------------------- embeddings

    def get_embeddings(self, model_id: str, hashes: Iterable[str]) -> dict[str, np.ndarray]:
        wanted = list(dict.fromkeys(hashes))
        result: dict[str, np.ndarray] = {}
        for offset in range(0, len(wanted), 500):
            batch = wanted[offset : offset + 500]
            rows = self._query(
                f"SELECT content_hash, vector FROM embeddings WHERE model_id = ? "
                f"AND content_hash IN ({_placeholders(len(batch))})",
                (model_id, *batch),
            )
            for row in rows:
                result[row["content_hash"]] = np.frombuffer(row["vector"], dtype=np.float32)
        return result

    def put_embeddings(self, model_id: str, vectors: dict[str, np.ndarray]) -> None:
        now = time.time()
        with self.transaction() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings"
                "(model_id, content_hash, dim, vector, created_at) VALUES (?, ?, ?, ?, ?)",
                [
                    (model_id, h, int(v.shape[0]), v.astype(np.float32).tobytes(), now)
                    for h, v in vectors.items()
                ],
            )

    def chunks_missing_embeddings(
        self, model_id: str, corpus_id: str, limit: int = 10_000
    ) -> list[tuple[str, str]]:
        rows = self._query(
            "SELECT DISTINCT c.content_hash, c.content FROM chunks c LEFT JOIN embeddings e "
            "ON e.model_id = ? AND e.content_hash = c.content_hash "
            "WHERE c.corpus_id = ? AND c.status = 'active' AND e.content_hash IS NULL LIMIT ?",
            (model_id, corpus_id, limit),
        )
        return [(r["content_hash"], r["content"]) for r in rows]

    def active_vectors(self, model_id: str, corpus_id: str) -> list[tuple[str, str, np.ndarray]]:
        rows = self._query(
            "SELECT c.chunk_id, c.source_type, e.vector FROM chunks c JOIN embeddings e "
            "ON e.model_id = ? AND e.content_hash = c.content_hash "
            "WHERE c.corpus_id = ? AND c.status = 'active'",
            (model_id, corpus_id),
        )
        return [
            (r["chunk_id"], r["source_type"], np.frombuffer(r["vector"], dtype=np.float32))
            for r in rows
        ]

    # ----------------------------------------------------------------- search

    def lexical_search(
        self,
        match_query: str,
        corpora: Sequence[str],
        source_types: Sequence[str],
        limit: int,
        restrict_chunk_ids: Sequence[str] | None = None,
    ) -> list[tuple[str, float]]:
        if not match_query or not corpora or not source_types:
            return []
        sql = (
            "SELECT c.chunk_id AS chunk_id, bm25(chunks_fts, 1.0, 4.0, 2.0) AS score "
            "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            f"WHERE chunks_fts MATCH ? AND c.corpus_id IN ({_placeholders(len(corpora))}) "
            f"AND c.source_type IN ({_placeholders(len(source_types))}) AND c.status = 'active'"
        )
        params: list[Any] = [match_query, *corpora, *source_types]
        if restrict_chunk_ids is not None:
            if not restrict_chunk_ids:
                return []
            sql += f" AND c.chunk_id IN ({_placeholders(len(restrict_chunk_ids))})"
            params.extend(restrict_chunk_ids)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        try:
            rows = self._query(sql, params)
        except sqlite3.OperationalError:
            return []
        # FTS5 bm25() is "lower is better"; expose a positive "higher is better" score.
        return [(r["chunk_id"], -float(r["score"])) for r in rows]

    def symbol_lookup(self, corpora: Sequence[str], names: Sequence[str]) -> list[sqlite3.Row]:
        if not corpora or not names:
            return []
        lowered = list(dict.fromkeys(n.split(".")[-1].lower() for n in names))
        return self._query(
            "SELECT s.*, c.status AS chunk_status FROM symbols s "
            "LEFT JOIN chunks c ON c.chunk_id = s.chunk_id "
            f"WHERE s.corpus_id IN ({_placeholders(len(corpora))}) "
            f"AND s.name_lower IN ({_placeholders(len(lowered))})",
            (*corpora, *lowered),
        )

    def path_lookup(
        self, corpora: Sequence[str], fragments: Sequence[str]
    ) -> list[tuple[str, str]]:
        """Return ``(corpus_id, path)`` for active files whose path ends with a fragment."""
        results: list[tuple[str, str]] = []
        if not corpora:
            return results
        for fragment in fragments:
            frag = fragment.strip("/").replace("\\", "/")
            if not frag:
                continue
            rows = self._query(
                f"SELECT corpus_id, path FROM files WHERE corpus_id IN "
                f"({_placeholders(len(corpora))}) AND status = 'active' "
                "AND (path = ? OR path LIKE ?) LIMIT 20",
                (*corpora, frag, f"%/{frag}"),
            )
            results.extend((r["corpus_id"], r["path"]) for r in rows)
        return list(dict.fromkeys(results))

    # ------------------------------------------------------------------ cache

    def cache_get(self, cache_key: str) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM retrieval_cache WHERE cache_key = ?", (cache_key,))
        return rows[0] if rows else None

    def cache_scope_entries(self, scope_key: str, limit: int = 20) -> list[sqlite3.Row]:
        return self._query(
            "SELECT * FROM retrieval_cache WHERE scope_key = ? ORDER BY created_at DESC LIMIT ?",
            (scope_key, limit),
        )

    def cache_put(
        self,
        cache_key: str,
        scope_key: str,
        state: dict[str, Any],
        fingerprint: dict[str, Any],
        result: dict[str, Any],
        changed_paths: list[str] | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO retrieval_cache(cache_key, scope_key, state_json, "
                "fingerprint_json, result_json, created_at, hits, changed_paths_json) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    cache_key,
                    scope_key,
                    json.dumps(state, sort_keys=True),
                    json.dumps(fingerprint, sort_keys=True),
                    json.dumps(result, sort_keys=True),
                    time.time(),
                    json.dumps(sorted(set(changed_paths))) if changed_paths is not None else None,
                ),
            )

    def cache_touch(self, cache_key: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE retrieval_cache SET hits = hits + 1, last_hit_at = ? WHERE cache_key = ?",
                (time.time(), cache_key),
            )

    def cache_prune(self, max_age_seconds: float, max_entries: int) -> int:
        cutoff = time.time() - max_age_seconds
        with self.transaction() as conn:
            removed = conn.execute(
                "DELETE FROM retrieval_cache WHERE COALESCE(last_hit_at, created_at) < ?",
                (cutoff,),
            ).rowcount
            removed += conn.execute(
                "DELETE FROM retrieval_cache WHERE cache_key NOT IN (SELECT cache_key FROM "
                "retrieval_cache ORDER BY COALESCE(last_hit_at, created_at) DESC LIMIT ?)",
                (max_entries,),
            ).rowcount
        return int(removed)

    def cache_clear(self, corpus_id: str | None = None) -> int:
        with self.transaction() as conn:
            if corpus_id is None:
                return int(conn.execute("DELETE FROM retrieval_cache").rowcount)
            return int(
                conn.execute(
                    "DELETE FROM retrieval_cache WHERE state_json LIKE ?",
                    (f'%"{corpus_id}"%',),
                ).rowcount
            )

    # ------------------------------------------------------------- rag mirror

    def mirror_state(self, corpus_id: str) -> dict[str, Any]:
        rows = self._query("SELECT * FROM rag_mirror_state WHERE corpus_id = ?", (corpus_id,))
        if not rows:
            return {
                "corpus_id": corpus_id,
                "cursor_seq": 0,
                "last_error": None,
                "last_attempt_at": None,
                "last_success_at": None,
            }
        return dict(rows[0])

    def set_mirror_state(self, corpus_id: str, **fields: Any) -> None:
        state = {**self.mirror_state(corpus_id), **fields, "corpus_id": corpus_id}
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO rag_mirror_state(corpus_id, cursor_seq, last_error, "
                "last_attempt_at, last_success_at) VALUES (?, ?, ?, ?, ?)",
                (
                    corpus_id,
                    state["cursor_seq"],
                    state["last_error"],
                    state["last_attempt_at"],
                    state["last_success_at"],
                ),
            )

    def mirror_rows(self, chunk_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        wanted = list(dict.fromkeys(chunk_ids))
        for offset in range(0, len(wanted), 500):
            batch = wanted[offset : offset + 500]
            for row in self._query(
                f"SELECT * FROM rag_mirror WHERE chunk_id IN ({_placeholders(len(batch))})", batch
            ):
                result[row["chunk_id"]] = dict(row)
        return result

    def mirror_upsert(
        self, corpus_id: str, chunk_id: str, document_id: str, version: int, content_hash: str
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO rag_mirror(chunk_id, corpus_id, remote_document_id, "
                "remote_version, content_hash, pushed_at) VALUES (?, ?, ?, ?, ?, ?)",
                (chunk_id, corpus_id, document_id, version, content_hash, time.time()),
            )

    def mirror_remove(self, chunk_id: str) -> None:
        """Forget the remote copy; keep the version counter so re-creation never reuses it."""
        with self.transaction() as conn:
            conn.execute(
                "UPDATE rag_mirror SET remote_document_id = NULL, content_hash = '', "
                "pushed_at = ? WHERE chunk_id = ?",
                (time.time(), chunk_id),
            )

    def mirror_gaps(self, corpus_id: str, fresh_after: float, limit: int = 500) -> list[str]:
        """Active chunks that rag-platform may not serve yet (never pushed, outdated, or pushed
        recently enough that asynchronous remote indexing may still be running)."""
        rows = self._query(
            "SELECT c.chunk_id FROM chunks c LEFT JOIN rag_mirror m ON m.chunk_id = c.chunk_id "
            "WHERE c.corpus_id = ? AND c.status = 'active' AND (m.chunk_id IS NULL OR "
            "m.remote_document_id IS NULL OR m.content_hash != c.content_hash OR "
            "m.pushed_at >= ?) LIMIT ?",
            (corpus_id, fresh_after, limit),
        )
        return [r["chunk_id"] for r in rows]

    def mirror_pending(self, corpus_id: str) -> int:
        cursor = int(self.mirror_state(corpus_id)["cursor_seq"])
        rows = self._query(
            "SELECT COUNT(DISTINCT chunk_id) AS n FROM changes WHERE corpus_id = ? AND seq > ?",
            (corpus_id, cursor),
        )
        return int(rows[0]["n"])

    # ------------------------------------------------------------------ stats

    def corpus_stats(self, corpus_id: str) -> dict[str, Any]:
        chunk_rows = self._query(
            "SELECT source_type, status, COUNT(*) AS n FROM chunks WHERE corpus_id = ? "
            "GROUP BY source_type, status",
            (corpus_id,),
        )
        file_rows = self._query(
            "SELECT source_type, status, COUNT(*) AS n FROM files WHERE corpus_id = ? "
            "GROUP BY source_type, status",
            (corpus_id,),
        )
        chunks: dict[str, dict[str, int]] = {}
        for row in chunk_rows:
            chunks.setdefault(row["source_type"], {})[row["status"]] = row["n"]
        files: dict[str, dict[str, int]] = {}
        for row in file_rows:
            files.setdefault(row["source_type"], {})[row["status"]] = row["n"]
        return {"chunks": chunks, "files": files}
