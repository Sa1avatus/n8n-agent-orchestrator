"""In-memory exact cosine index over active chunk embeddings, updated incrementally.

The index follows the store's change log: a change to one chunk masks its old row and appends the
new vector instead of reloading the corpus. Rows are compacted when more than a third are dead.
Exact search is adequate for repository-scale corpora (≈100k chunks); an ANN backend can replace
this class behind the same interface when evaluation data shows the need.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from .store import ACTIVE, Store


@dataclass
class _CorpusIndex:
    ids: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)
    matrix: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.float32))
    alive: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    row_of: dict[str, int] = field(default_factory=dict)
    seq: int = 0


class VectorIndex:
    def __init__(self, store: Store, model_id: str) -> None:
        self.store = store
        self.model_id = model_id
        self._indexes: dict[str, _CorpusIndex] = {}
        self._lock = threading.Lock()

    def _load(self, corpus_id: str) -> _CorpusIndex:
        seq = self.store.max_change_seq()
        rows = self.store.active_vectors(self.model_id, corpus_id)
        index = _CorpusIndex(seq=seq)
        if rows:
            index.ids = [r[0] for r in rows]
            index.source_types = [r[1] for r in rows]
            index.matrix = np.vstack([r[2] for r in rows]).astype(np.float32)
            index.alive = np.ones(len(rows), dtype=bool)
            index.row_of = {chunk_id: i for i, chunk_id in enumerate(index.ids)}
        return index

    def _refresh(self, corpus_id: str) -> _CorpusIndex:
        index = self._indexes.get(corpus_id)
        if index is None:
            index = self._load(corpus_id)
            self._indexes[corpus_id] = index
            return index
        # Embedding back-fill after an embedder outage has no chunk change; the indexer calls
        # ``invalidate`` for that case.
        changes = self.store.changes_since(index.seq, corpus_id=corpus_id)
        if not changes:
            return index
        index.seq = changes[-1].seq
        touched = list(dict.fromkeys(c.chunk_id for c in changes))
        for chunk_id in touched:
            row = index.row_of.pop(chunk_id, None)
            if row is not None:
                index.alive[row] = False
        chunks = self.store.get_chunks(touched)
        active = [c for c in chunks.values() if c.status == ACTIVE]
        vectors = self.store.get_embeddings(self.model_id, [c.content_hash for c in active])
        new_rows = [
            (c.chunk_id, c.source_type, vectors[c.content_hash])
            for c in active
            if c.content_hash in vectors
        ]
        if new_rows:
            start = len(index.ids)
            block = np.vstack([r[2] for r in new_rows]).astype(np.float32)
            index.matrix = block if index.matrix.size == 0 else np.vstack([index.matrix, block])
            index.alive = np.concatenate([index.alive, np.ones(len(new_rows), dtype=bool)])
            for offset, (chunk_id, source_type, _) in enumerate(new_rows):
                index.ids.append(chunk_id)
                index.source_types.append(source_type)
                index.row_of[chunk_id] = start + offset
        if index.alive.size and (~index.alive).sum() > index.alive.size / 3:
            index = self._load(corpus_id)
            self._indexes[corpus_id] = index
        return index

    def invalidate(self, corpus_id: str | None = None) -> None:
        """Drop cached matrices (used after embedding back-fill)."""
        with self._lock:
            if corpus_id is None:
                self._indexes.clear()
            else:
                self._indexes.pop(corpus_id, None)

    def search(
        self,
        query_vector: np.ndarray,
        corpora: Sequence[str],
        source_types: Sequence[str],
        limit: int,
    ) -> list[tuple[str, float]]:
        results: list[tuple[str, float]] = []
        allowed = set(source_types)
        with self._lock:
            for corpus_id in corpora:
                index = self._refresh(corpus_id)
                if index.matrix.size == 0 or index.matrix.shape[1] != query_vector.shape[0]:
                    continue
                scores = index.matrix @ query_vector.astype(np.float32)
                mask = index.alive.copy()
                if allowed != {"code", "doc", "history"}:
                    mask &= np.array([st in allowed for st in index.source_types], dtype=bool)
                scores = np.where(mask, scores, -np.inf)
                take = min(limit, int(mask.sum()))
                if take <= 0:
                    continue
                top = np.argpartition(-scores, take - 1)[:take]
                results.extend((index.ids[i], float(scores[i])) for i in top)
        results.sort(key=lambda item: (-item[1], item[0]))
        return results[:limit]
