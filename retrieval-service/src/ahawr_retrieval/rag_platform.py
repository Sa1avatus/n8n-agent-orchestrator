"""rag-platform as an optional external retrieval backend.

The AHAWR layer always keeps its own local index (the source of truth for provenance, freshness,
symbols and the change log). When rag-platform is configured, active chunks are mirrored into one
rag-platform collection as individual documents:

* ``external_document_id`` = AHAWR ``chunk_id`` (stable across edits of other chunks);
* ``version`` increases on every content change of that chunk, so only changed chunks are
  re-embedded by rag-platform;
* ``metadata`` carries provenance (``ahawr_corpus``, ``ahawr_chunk_id``, ``ahawr_content_hash``,
  ``source_type``, ``path``, ``symbol``, lines). ``ahawr_corpus`` is used as a search filter,
  which rag-platform applies inside both BM25 and dense stages.

No rag-platform code or schema change is required: only its public ``/v1`` service API is used.

Retrieval asks rag-platform for separate lexical and dense rankings (its reranker disabled so that
both backends share one reranking path), then every remote hit is validated against the local
store: a hit whose content hash differs from the current local chunk is dropped as stale. Because
rag-platform indexes asynchronously, chunks changed within the freshness window are also searched
locally ("fresh" retriever). Any transport failure opens a circuit breaker and the request falls
back to the local backend.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Settings
from .store import ACTIVE, DELETED, ChunkRecord, Store


class RagPlatformUnavailable(RuntimeError):
    pass


@dataclass
class RemoteHits:
    lexical: list[tuple[str, float]] = field(default_factory=list)
    vector: list[tuple[str, float]] = field(default_factory=list)
    content_hashes: dict[str, str] = field(default_factory=dict)
    latency_ms: float = 0.0


class RagPlatformClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.rag_configured:
            raise ValueError("rag-platform backend is not fully configured")
        assert settings.rag_url and settings.rag_api_key and settings.rag_owner_id
        self.base = settings.rag_url.rstrip("/")
        self.project_id = settings.rag_project_id
        self.collection = settings.rag_collection
        self.cooldown = settings.rag_cooldown_seconds
        self._client = client or httpx.Client(
            timeout=settings.rag_timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.rag_api_key}",
                "X-Owner-User-Id": settings.rag_owner_id,
            },
        )
        self._open_until = 0.0
        self._last_error: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------- circuit breaker

    def available(self) -> bool:
        return time.time() >= self._open_until

    def _fail(self, exc: Exception) -> RagPlatformUnavailable:
        with self._lock:
            self._open_until = time.time() + self.cooldown
            self._last_error = f"{type(exc).__name__}: {exc}"[:300]
        return RagPlatformUnavailable(self._last_error)

    def _ok(self) -> None:
        with self._lock:
            self._open_until = 0.0

    def status(self) -> dict[str, Any]:
        return {
            "configured": True,
            "url": self.base,
            "collection": self.collection,
            "circuit_open": not self.available(),
            "last_error": self._last_error,
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if not self.available():
            raise RagPlatformUnavailable("circuit open")
        try:
            response = self._client.request(method, f"{self.base}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise self._fail(exc) from exc
        if response.status_code >= 500:
            raise self._fail(
                httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
            )
        self._ok()
        return response

    # ----------------------------------------------------------------- search

    def search(
        self,
        corpus_id: str,
        lexical_query: str,
        vector_query: str,
        source_types: Sequence[str],
        lexical_k: int,
        vector_k: int,
    ) -> RemoteHits:
        started = time.perf_counter()
        filters: dict[str, Any] = {"ahawr_corpus": corpus_id}
        if len(source_types) == 1:
            filters["source_type"] = source_types[0]
        hits = RemoteHits()
        for mode, text, k, target in (
            ("lexical", lexical_query, lexical_k, hits.lexical),
            ("dense", vector_query, vector_k, hits.vector),
        ):
            if not text.strip():
                continue
            response = self._request(
                "POST",
                "/v1/retrieval/search",
                json={
                    "project_id": self.project_id,
                    "collections": [self.collection],
                    "query": text[:10_000],
                    "mode": mode,
                    "filters": filters,
                    "vector_top_k": min(max(k, 1), 200),
                    "bm25_top_k": min(max(k, 1), 200),
                    "fusion_top_k": min(max(k, 1), 100),
                    "rerank_top_k": min(max(k, 1), 50),
                    "use_reranker": False,
                    "include_parent_content": False,
                },
            )
            if response.status_code >= 400:
                raise self._fail(ValueError(f"search HTTP {response.status_code}"))
            score_key = "bm25_score" if mode == "lexical" else "vector_score"
            seen: set[str] = set()
            for item in response.json().get("results", []):
                metadata = item.get("metadata") or {}
                chunk_id = str(metadata.get("ahawr_chunk_id") or item.get("external_document_id"))
                if not chunk_id or chunk_id in seen or metadata.get("ahawr_corpus") != corpus_id:
                    continue
                seen.add(chunk_id)
                score = item.get(score_key)
                if score is None:
                    score = item.get("fusion_score", 0.0)
                target.append((chunk_id, float(score)))
                hits.content_hashes[chunk_id] = str(metadata.get("ahawr_content_hash", ""))
        hits.latency_ms = (time.perf_counter() - started) * 1000
        return hits

    # ------------------------------------------------------------- documents

    def create_documents(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for offset in range(0, len(documents), 100):
            batch = documents[offset : offset + 100]
            response = self._request("POST", "/v1/documents/batch", json={"documents": batch})
            if response.status_code == 409:
                # The batch endpoint stops at the first conflict: fall back to single creates.
                for document in batch:
                    created.append(self._create_one(document))
                continue
            if response.status_code >= 400:
                raise self._fail(ValueError(f"batch HTTP {response.status_code}"))
            created.extend(response.json())
        return created

    def _create_one(self, document: dict[str, Any]) -> dict[str, Any]:
        payload = dict(document)
        for _ in range(3):
            response = self._request("POST", "/v1/documents", json=payload)
            if response.status_code == 409:
                payload["version"] = int(payload["version"]) + 1
                continue
            if response.status_code >= 400:
                raise self._fail(ValueError(f"create HTTP {response.status_code}"))
            result: dict[str, Any] = response.json()
            return result
        raise self._fail(ValueError("create conflict persisted"))

    def delete_document(self, document_id: str) -> None:
        response = self._request("DELETE", f"/v1/documents/{document_id}")
        if response.status_code >= 400 and response.status_code != 404:
            raise self._fail(ValueError(f"delete HTTP {response.status_code}"))


class RagMirror:
    """Pushes the local change log to rag-platform. Failures never block local indexing."""

    def __init__(self, store: Store, client: RagPlatformClient) -> None:
        self.store = store
        self.client = client
        self._lock = threading.Lock()

    def push(self, corpus_id: str) -> dict[str, Any]:
        with self._lock:
            state = self.store.mirror_state(corpus_id)
            changes = self.store.changes_since(int(state["cursor_seq"]), corpus_id=corpus_id)
            if not changes:
                return {"pushed": 0, "deleted": 0, "pending": 0}
            now = time.time()
            self.store.set_mirror_state(corpus_id, last_attempt_at=now)
            chunk_ids = list(dict.fromkeys(c.chunk_id for c in changes))
            records = self.store.get_chunks(chunk_ids)
            mirror = self.store.mirror_rows(chunk_ids)
            creates: list[tuple[ChunkRecord, int, bool]] = []
            deletes: list[str] = []
            for chunk_id in chunk_ids:
                record = records.get(chunk_id)
                row = mirror.get(chunk_id) or {}
                remote_id = row.get("remote_document_id")
                if record is not None and record.status == ACTIVE:
                    if remote_id and row.get("content_hash") == record.content_hash:
                        continue
                    version = int(row.get("remote_version", 0)) + 1
                    creates.append((record, version, bool(row) and not remote_id))
                elif record is not None and record.status == DELETED and remote_id:
                    # Invalidated chunks stay remote: local validation filters them, and a
                    # remote delete is permanent for that external id.
                    deletes.append(chunk_id)
            try:
                results = self.client.create_documents(
                    [
                        self._payload(record, version, fresh_id)
                        for record, version, fresh_id in creates
                    ]
                )
                for (record, version, _), result in zip(creates, results, strict=True):
                    self.store.mirror_upsert(
                        corpus_id,
                        record.chunk_id,
                        str(result["id"]),
                        int(result.get("version", version)),
                        record.content_hash,
                    )
                for chunk_id in deletes:
                    self.client.delete_document(str(mirror[chunk_id]["remote_document_id"]))
                    self.store.mirror_remove(chunk_id)
            except RagPlatformUnavailable as exc:
                self.store.set_mirror_state(corpus_id, last_error=str(exc))
                raise
            self.store.set_mirror_state(
                corpus_id, cursor_seq=changes[-1].seq, last_error=None, last_success_at=now
            )
            return {"pushed": len(creates), "deleted": len(deletes), "pending": 0}

    def _payload(self, record: ChunkRecord, version: int, fresh_id: bool) -> dict[str, Any]:
        label = (record.symbol or record.section or record.anchor).split("#")[0]
        # rag-platform deletions are soft and permanent per external id, so a chunk that
        # reappears after a remote delete gets a new external id.
        external_id = f"{record.chunk_id}.v{version}" if fresh_id else record.chunk_id
        return {
            "project_id": self.client.project_id,
            "collection": self.client.collection,
            "external_document_id": external_id,
            "document_type": f"ahawr-{record.source_type}",
            "title": f"{record.path} :: {label}"[:500],
            "content": record.content,
            "language": "und",
            "version": version,
            "metadata": {
                "ahawr_corpus": record.corpus_id,
                "ahawr_chunk_id": record.chunk_id,
                "ahawr_content_hash": record.content_hash,
                "source_type": record.source_type,
                "path": record.path,
                "symbol": (record.symbol or "").split("#")[0],
                "section": record.section or "",
                "start_line": record.start_line,
                "end_line": record.end_line,
            },
        }
