"""Retrieval Service composition root and the /retrieve pipeline.

Pipeline: freshness sync → query build → cache lookup → lexical / vector / symbol retrieval →
weighted RRF candidate merge → hydrate → deterministic hard filters → text-only cross-encoder
rerank → deterministic ranking layer → budgeted context assembly → cache store → feature log.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence
from typing import Any

import numpy as np

from . import __version__
from .cache import CacheLookup, RetrievalCache, scope_key
from .candidates import Candidate, fuse
from .config import Settings, map_host_path
from .context import render_context, select_context
from .embeddings import Embedder, EmbeddingError, HashingEmbedder, OpenAICompatibleEmbedder
from .filters import apply_hard_filters
from .indexer import Indexer, IndexingError, WorkspaceVerifier
from .logstore import RetrievalLog
from .models import (
    CacheInfo,
    ChunkScores,
    IndexRequest,
    IndexResponse,
    InvalidateRequest,
    InvalidateResponse,
    RetrievedChunk,
    RetrieveRequest,
    RetrieveResponse,
)
from .profiles import Profile, load_profiles
from .query_builder import BuiltQuery, build_query
from .rag_platform import RagMirror, RagPlatformClient, RagPlatformUnavailable
from .ranking import AUTHORITY, rank_candidates
from .reranker import HttpReranker, NoopReranker, Reranker
from .store import CorpusRecord, FileRecord, Store
from .text import content_terms, extract_identifiers, sha256_hex
from .vector_index import VectorIndex

MAX_FTS_TERMS = 64


class RetrievalError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "openai":
        if not settings.embedding_url or not settings.embedding_model:
            raise ValueError("RETRIEVAL_EMBEDDING_URL and RETRIEVAL_EMBEDDING_MODEL are required")
        return OpenAICompatibleEmbedder(
            settings.embedding_url,
            settings.embedding_model,
            api_key=settings.embedding_api_key,
            query_prefix=settings.embedding_query_prefix,
            passage_prefix=settings.embedding_passage_prefix,
            batch_size=settings.embedding_batch_size,
        )
    if settings.embedder == "hashing":
        return HashingEmbedder(settings.embedding_dim)
    raise ValueError(f"unknown embedder {settings.embedder!r}")


def build_reranker(settings: Settings) -> Reranker:
    if not settings.reranker_url:
        return NoopReranker()
    return HttpReranker(
        settings.reranker_url,
        api_key=settings.reranker_api_key,
        timeout_seconds=settings.reranker_timeout_seconds,
        max_chars=settings.reranker_max_chars,
    )


def fts_match_query(query: BuiltQuery) -> str:
    terms: dict[str, None] = {}
    for identifier in extract_identifiers(query.lexical_text):
        for part in identifier.split("."):
            if len(part) >= 3:
                terms.setdefault(part.lower(), None)
    for term in content_terms(query.lexical_text):
        terms.setdefault(term, None)
    selected = list(terms)[:MAX_FTS_TERMS]
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in selected)


class RetrievalService:
    def __init__(
        self,
        settings: Settings,
        *,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        profiles: dict[str, Profile] | None = None,
    ) -> None:
        self.settings = settings
        self.store = Store(settings.store_path)
        self.log = RetrievalLog(settings.log_path)
        self.embedder = embedder or build_embedder(settings)
        self.reranker = reranker or build_reranker(settings)
        self.profiles = profiles or load_profiles(settings.profiles_file)
        self.vector_index = VectorIndex(self.store, self.embedder.model_id)
        self.indexer = Indexer(self.store, self.embedder, self.vector_index, settings)
        self.cache = RetrievalCache(
            self.store, settings.cache_ttl_seconds, settings.cache_max_entries
        )
        self.verifier = WorkspaceVerifier()
        self.rag: RagPlatformClient | None = (
            RagPlatformClient(settings, state=self.store) if settings.rag_configured else None
        )
        self.mirror = RagMirror(self.store, self.rag) if self.rag and settings.rag_mirror else None

    def close(self) -> None:
        self.store.close()
        self.log.close()

    # ------------------------------------------------------------- index API

    def index(self, request: IndexRequest) -> IndexResponse:
        if request.root and self.settings.path_map:
            request = request.model_copy(
                update={"root": map_host_path(request.root, self.settings.path_map)}
            )
        response = self.indexer.index(request)
        response.degraded_reasons = sorted(
            set(response.degraded_reasons) | set(self._push_mirror(request.corpus_id))
        )
        return response

    def invalidate(self, request: InvalidateRequest) -> InvalidateResponse:
        response = self.indexer.invalidate(request)
        self._push_mirror(request.corpus_id)
        return response

    def _push_mirror(self, corpus_id: str) -> list[str]:
        """Mirror local changes to rag-platform; failure leaves them pending for the next push."""
        if self.mirror is None:
            return []
        try:
            self.mirror.push(corpus_id)
        except RagPlatformUnavailable:
            return ["rag_mirror_pending"]
        return []

    def backend_status(self) -> dict[str, Any]:
        if self.rag is None:
            return {"default": self.settings.backend, "rag_platform": {"configured": False}}
        mirrors = (
            {
                c.corpus_id: {
                    **self.store.mirror_state(c.corpus_id),
                    "pending_chunks": self.store.mirror_pending(c.corpus_id),
                }
                for c in self.store.list_corpora()
            }
            if self.mirror
            else {}
        )
        return {
            "default": self.settings.backend,
            "rag_platform": {**self.rag.status(), "mirror": mirrors},
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "embedder": self.embedder.model_id,
            "reranker": self.reranker.name,
            "profiles": sorted(self.profiles),
            "corpora": len(self.store.list_corpora()),
            "workspace_indexing": bool(self.settings.allowed_roots),
            "backend": self.backend_status(),
        }

    def corpus_info(self, corpus_id: str) -> dict[str, Any]:
        corpus = self.store.get_corpus(corpus_id)
        if corpus is None:
            raise RetrievalError(f"unknown corpus {corpus_id!r}", 404)
        return {
            **self._snapshot(corpus),
            "corpus_id": corpus_id,
            "root": corpus.root,
            **self.store.corpus_stats(corpus_id),
        }

    # ---------------------------------------------------------- retrieve API

    def resolve_profile(self, request: RetrieveRequest) -> Profile:
        base = self.profiles.get(request.profile)
        if base is None:
            raise RetrievalError(
                f"unknown profile {request.profile!r}; available: {sorted(self.profiles)}"
            )
        overrides: dict[str, Any] = dict(request.options)
        if request.budget:
            budget = dict(overrides.get("budget", {}))
            budget.update(request.budget.model_dump(exclude_none=True))
            overrides["budget"] = budget
        try:
            return base.with_overrides(overrides)
        except ValueError as exc:
            raise RetrievalError(f"invalid options: {exc}") from exc

    def retrieve(self, request: RetrieveRequest) -> RetrieveResponse:
        started = time.perf_counter()
        timings: dict[str, float] = {}
        request_id = "rr_" + uuid.uuid4().hex[:20]
        profile = self.resolve_profile(request)
        freshness_mode = request.freshness_mode or profile.freshness_mode
        degraded: list[str] = []

        mark = time.perf_counter()
        corpora = self._corpora(request.corpora, request.corpus_roots)
        if freshness_mode == "sync":
            for corpus_id, corpus in list(corpora.items()):
                if not corpus.root:
                    continue
                try:
                    result = self.indexer.sync(corpus_id)
                    if result is not None:
                        degraded.extend(result.degraded_reasons)
                        degraded.extend(self._push_mirror(corpus_id))
                except IndexingError as exc:
                    degraded.append(f"sync_failed:{corpus_id}:{exc}")
            corpora = self._corpora(request.corpora, request.corpus_roots)
        timings["sync"] = _ms(mark)

        mark = time.perf_counter()
        query = build_query(request)
        match_query = fts_match_query(query)
        timings["query_build"] = _ms(mark)
        source_types: list[str] = list(profile.source_types)
        state = {
            cid: {st: c.generation(st) for st in ("code", "doc") if st in source_types}
            for cid, c in corpora.items()
        }
        trace = request.trace.model_dump(exclude_none=True) if request.trace else {}
        scope = scope_key(
            profile,
            list(corpora),
            request.task.mission_id or trace.get("mission_id"),
            request.task.task_id or trace.get("task_id"),
        )
        query_vector: dict[str, np.ndarray] = {}

        def vector_for_query() -> np.ndarray:
            if "v" not in query_vector:
                query_vector["v"] = self.embedder.embed_query(query.vector_text)
            return query_vector["v"]

        cache_info = CacheInfo(status="bypass" if request.cache == "bypass" else "miss")
        if request.cache == "refresh":
            cache_info = CacheInfo(status="refresh")
        use_cache = profile.cache.enabled and request.cache != "bypass"
        if use_cache and request.cache == "use":
            mark = time.perf_counter()
            lookup = self.cache.lookup(
                scope,
                state,
                query,
                profile,
                lambda ids: self._delta_scores(
                    ids, match_query, vector_for_query, profile, list(corpora)
                ),
            )
            timings["cache_lookup"] = _ms(mark)
            if lookup.result is not None:
                cached = self._from_cache(
                    lookup,
                    request,
                    request_id,
                    profile,
                    query,
                    corpora,
                    freshness_mode,
                    degraded,
                    timings,
                    started,
                    trace,
                )
                if cached is not None:
                    return cached
                cache_info = CacheInfo(
                    status="miss", key=lookup.cache_key, reason="cached_chunk_invalid"
                )
            else:
                cache_info = CacheInfo(status="miss", key=lookup.cache_key, reason=lookup.reason)

        retrievers = profile.retrievers
        corpus_ids = list(corpora)
        results, remote_hashes, backend_used = self._candidate_lists(
            profile,
            query,
            match_query,
            corpus_ids,
            source_types,
            vector_for_query,
            degraded,
            timings,
        )
        if retrievers.symbol:
            mark = time.perf_counter()
            results["symbol"] = self._symbol_search(
                query, match_query, corpus_ids, source_types, retrievers.symbol_k
            )
            timings["symbol"] = _ms(mark)

        mark = time.perf_counter()
        candidates = fuse(results, retrievers.weights, retrievers.rrf_k)[: profile.pool_size]
        self._hydrate(candidates)
        timings["fusion"] = _ms(mark)

        mark = time.perf_counter()
        filtered = apply_hard_filters(
            candidates,
            corpora=corpora,
            profile=profile,
            workspace_state=request.workspace_state,
            freshness_mode=freshness_mode,
            verifier=self.verifier,
        )
        local_ids = {cid for name in ("symbol", "fresh") for cid, _ in results.get(name, [])}
        for candidate in candidates:
            remote = remote_hashes.get(candidate.chunk_id)
            if (
                remote is not None
                and candidate.record is not None
                and not candidate.filtered_reason
                and remote != candidate.record.content_hash
                and candidate.chunk_id not in local_ids
            ):
                # rag-platform ranked an older version of this chunk: its score is not evidence.
                candidate.filtered_reason = "remote_stale"
                filtered["remote_stale"] = filtered.get("remote_stale", 0) + 1
        valid = [c for c in candidates if not c.filtered_reason]
        timings["filters"] = _ms(mark)

        reranked = False
        n_reranked = 0
        if profile.rerank.enabled and valid:
            window = valid[: profile.rerank.top_n]
            outcome = self.reranker.rerank(
                query.rerank_text,
                [(c.chunk_id, self._rerank_text(c, profile)) for c in window],
            )
            timings["rerank"] = round(outcome.latency_ms, 2)
            if outcome.degraded_reason:
                degraded.append(outcome.degraded_reason)
            elif outcome.scores:
                reranked = True
                for candidate in window:
                    if candidate.chunk_id in outcome.scores:
                        raw, normalized = outcome.scores[candidate.chunk_id]
                        candidate.reranker_raw = round(raw, 6)
                        candidate.reranker = round(normalized, 6)
                n_reranked = sum(c.reranker is not None for c in window)
                order = sorted(
                    (c for c in window if c.reranker is not None),
                    key=lambda c: (-(c.reranker or 0.0), c.fused_rank),
                )
                for rank, candidate in enumerate(order, start=1):
                    candidate.reranker_rank = rank

        mark = time.perf_counter()
        ranked = rank_candidates(valid, query, profile, reranked)
        selected = select_context(
            ranked, profile.budget, profile.budget.max_chunks, profile.budget.max_tokens
        )
        timings["ranking"] = _ms(mark)

        chunks = [self._chunk_out(c, rank, corpora) for rank, c in enumerate(selected, 1)]
        context = (
            render_context(selected, profile.name, request_id) if request.render_context else ""
        )
        context_tokens = sum(c.token_count for c in chunks)
        if use_cache and selected:
            cache_info.key = self.cache.put(
                scope,
                state,
                query,
                {
                    "request_id": request_id,
                    "selected": [self._cache_item(c) for c in selected],
                    "degraded_reasons": sorted(set(degraded)),
                },
            )
        timings["total"] = _ms(started)
        stats = {
            "candidates": len(candidates),
            "filtered": sum(filtered.values()),
            "filtered_by_reason": filtered,
            "reranked": n_reranked,
            "selected": len(selected),
            "retrievers": {name: len(items) for name, items in results.items()},
            "backend": backend_used,
        }
        response = RetrieveResponse(
            request_id=request_id,
            profile=profile.name,
            config_id=profile.config_id(),
            query=self._query_out(query, freshness_mode),
            cache=cache_info,
            snapshots={cid: self._snapshot(c) for cid, c in corpora.items()},
            degraded=bool(degraded),
            degraded_reasons=sorted(set(degraded)),
            chunks=chunks,
            context=context,
            context_tokens=context_tokens,
            stats=stats,
            timings_ms=timings,
            candidates=[c.log_row(time.time()) for c in candidates]
            if request.include_candidates
            else None,
        )
        self._log(response, request, profile, query, candidates, n_reranked, trace)
        return response

    # -------------------------------------------------------------- helpers

    def _corpora(
        self, corpus_ids: list[str], roots: dict[str, str] | None = None
    ) -> dict[str, CorpusRecord]:
        corpora: dict[str, CorpusRecord] = {}
        missing = []
        for corpus_id in corpus_ids:
            corpus = self.store.get_corpus(corpus_id)
            root = self.settings.bootstrap_corpora.get(corpus_id) or (roots or {}).get(corpus_id)
            if corpus is None and root:
                # First use of a corpus declared by the deployment: index it now.
                self.index(IndexRequest(corpus_id=corpus_id, root=root))
                corpus = self.store.get_corpus(corpus_id)
            if corpus is None:
                missing.append(corpus_id)
            else:
                corpora[corpus_id] = corpus
        if missing:
            raise RetrievalError(f"unknown corpora: {missing}; index them first", 404)
        return corpora

    def _candidate_lists(
        self,
        profile: Profile,
        query: BuiltQuery,
        match_query: str,
        corpus_ids: list[str],
        source_types: Sequence[str],
        vector_for_query: Any,
        degraded: list[str],
        timings: dict[str, float],
    ) -> tuple[dict[str, list[tuple[str, float]]], dict[str, str], str]:
        """Lexical and dense candidate lists from rag-platform (if selected and reachable) or
        from the local index. Returns ``(lists, remote content hashes, backend name)``."""
        retrievers = profile.retrievers
        backend = profile.backend if profile.backend != "auto" else self.settings.backend
        results: dict[str, list[tuple[str, float]]] = {}
        if backend in {"auto", "rag_platform"} and (retrievers.lexical or retrievers.vector):
            if self.rag is None:
                if backend == "rag_platform":
                    degraded.append("rag_platform_not_configured:fallback_local")
            elif not self.rag.available():
                degraded.append("rag_platform_unavailable:fallback_local")
            else:
                mark = time.perf_counter()
                try:
                    remote_hashes: dict[str, str] = {}
                    lexical: list[tuple[str, float]] = []
                    vector: list[tuple[str, float]] = []
                    for corpus_id in corpus_ids:
                        hits = self.rag.search(
                            corpus_id,
                            query.lexical_text if retrievers.lexical else "",
                            query.vector_text if retrievers.vector else "",
                            source_types,
                            retrievers.lexical_k,
                            retrievers.vector_k,
                        )
                        lexical.extend(hits.lexical)
                        vector.extend(hits.vector)
                        remote_hashes.update(hits.content_hashes)
                    if retrievers.lexical:
                        results["lexical"] = sorted(lexical, key=lambda i: -i[1])
                    if retrievers.vector:
                        results["vector"] = sorted(vector, key=lambda i: -i[1])
                    if self.mirror is not None:
                        fresh_after = time.time() - self.settings.rag_fresh_window_seconds
                        gaps = [
                            cid
                            for corpus_id in corpus_ids
                            for cid in self.store.mirror_gaps(corpus_id, fresh_after)
                        ]
                        if gaps:
                            results["fresh"] = self.store.lexical_search(
                                match_query,
                                corpus_ids,
                                source_types,
                                retrievers.lexical_k,
                                restrict_chunk_ids=gaps,
                            )
                    timings["rag_platform"] = _ms(mark)
                    return results, remote_hashes, "rag_platform"
                except RagPlatformUnavailable:
                    degraded.append("rag_platform_unavailable:fallback_local")
                    results = {}
                timings["rag_platform"] = _ms(mark)
        if retrievers.lexical:
            mark = time.perf_counter()
            results["lexical"] = self.store.lexical_search(
                match_query, corpus_ids, source_types, retrievers.lexical_k
            )
            timings["lexical"] = _ms(mark)
        if retrievers.vector:
            mark = time.perf_counter()
            try:
                results["vector"] = self.vector_index.search(
                    vector_for_query(), corpus_ids, source_types, retrievers.vector_k
                )
            except EmbeddingError:
                degraded.append("vector_unavailable")
            timings["vector"] = _ms(mark)
        return results, {}, "local"

    def _symbol_search(
        self,
        query: BuiltQuery,
        match_query: str,
        corpora: list[str],
        source_types: Sequence[str],
        limit: int,
    ) -> list[tuple[str, float]]:
        scores: dict[str, float] = {}
        identifiers = query.identifiers
        if identifiers:
            exact_names = {i.split(".")[-1] for i in identifiers}
            qualified = {i.lower() for i in identifiers if "." in i}
            for row in self.store.symbol_lookup(corpora, identifiers):
                if not row["chunk_id"] or row["chunk_status"] != "active":
                    continue
                score = 1.0 if row["name"] in exact_names else 0.85
                if row["qualname"].lower() in qualified:
                    score = 1.05
                scores[row["chunk_id"]] = max(scores.get(row["chunk_id"], 0.0), score)
        fragments = [p for p in query.paths if "/" in p or "." in p]
        for corpus_id, path in self.store.path_lookup(corpora, fragments):
            chunks = self.store.active_chunks_for_paths(corpus_id, [path], 200)
            if not chunks:
                continue
            ids = [c.chunk_id for c in chunks]
            best = [
                cid
                for cid, _ in self.store.lexical_search(
                    match_query, [corpus_id], source_types, 3, restrict_chunk_ids=ids
                )
            ]
            for position, chunk_id in enumerate(best or ids[:2]):
                score = 0.9 - 0.05 * position
                scores[chunk_id] = max(scores.get(chunk_id, 0.0), score)
        if not scores:
            return []
        records = self.store.get_chunks(scores)
        allowed = set(source_types)
        ordered = sorted(
            (
                (cid, s)
                for cid, s in scores.items()
                if cid in records and records[cid].source_type in allowed
            ),
            key=lambda item: (-item[1], item[0]),
        )
        return ordered[:limit]

    def _delta_scores(
        self,
        chunk_ids: list[str],
        match_query: str,
        vector_for_query: Any,
        profile: Profile,
        corpora: list[str],
    ) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        types = list(profile.source_types)
        if profile.retrievers.lexical:
            for cid, score in self.store.lexical_search(
                match_query, corpora, types, len(chunk_ids), restrict_chunk_ids=chunk_ids
            ):
                out.setdefault(cid, {})["lexical"] = score
        if profile.retrievers.vector:
            records = self.store.get_chunks(chunk_ids)
            vectors = self.store.get_embeddings(
                self.embedder.model_id, [r.content_hash for r in records.values()]
            )
            try:
                q = vector_for_query()
            except EmbeddingError:
                return out
            for cid, record in records.items():
                vector = vectors.get(record.content_hash)
                if vector is not None and vector.shape == q.shape:
                    out.setdefault(cid, {})["vector"] = float(vector @ q)
        return out

    def _hydrate(self, candidates: list[Candidate]) -> None:
        records = self.store.get_chunks(c.chunk_id for c in candidates)
        by_corpus: dict[str, set[str]] = {}
        for record in records.values():
            by_corpus.setdefault(record.corpus_id, set()).add(record.path)
        files: dict[tuple[str, str], FileRecord] = {}
        for corpus_id, paths in by_corpus.items():
            for path, file in self.store.get_files(corpus_id, paths).items():
                files[(corpus_id, path)] = file
        for candidate in candidates:
            candidate.record = records.get(candidate.chunk_id)
            if candidate.record is not None:
                candidate.file = files.get((candidate.record.corpus_id, candidate.record.path))

    def _rerank_text(self, candidate: Candidate, profile: Profile) -> str:
        record = candidate.record
        assert record is not None
        if not profile.rerank.include_header:
            return record.content
        header = record.path + (f" :: {record.symbol}" if record.symbol else "")
        return f"{header}\n{record.content}"

    def _chunk_out(
        self, candidate: Candidate, rank: int, corpora: dict[str, CorpusRecord]
    ) -> RetrievedChunk:
        record, file = candidate.record, candidate.file
        assert record is not None and file is not None
        corpus = corpora[record.corpus_id]
        return RetrievedChunk(
            rank=rank,
            chunk_id=record.chunk_id,
            corpus_id=record.corpus_id,
            source_type=record.source_type,
            authority=AUTHORITY.get(record.source_type, record.source_type),
            path=record.path,
            document=file.title or record.path,
            symbol=record.symbol.split("#")[0] if record.symbol else None,
            symbol_kind=record.symbol_kind,
            section=record.section,
            language=record.language,
            start_line=record.start_line,
            end_line=record.end_line,
            content=record.content,
            content_hash=f"sha256:{record.content_hash}",
            file_hash=f"sha256:{record.file_hash}",
            version=record.version,
            snapshot_id=record.snapshot_id,
            generation=corpus.generation(record.source_type),
            indexed_at=record.indexed_at,
            token_count=record.token_count,
            freshness="verified" if candidate.freshness == "verified" else "indexed",
            scores=ChunkScores(
                lexical=candidate.scores.get("lexical"),
                lexical_rank=candidate.ranks.get("lexical"),
                vector=candidate.scores.get("vector"),
                vector_rank=candidate.ranks.get("vector"),
                symbol=candidate.scores.get("symbol"),
                symbol_rank=candidate.ranks.get("symbol"),
                fused=round(candidate.fused_norm, 6),
                fused_rank=candidate.fused_rank,
                reranker=candidate.reranker,
                reranker_raw=candidate.reranker_raw,
                reranker_rank=candidate.reranker_rank,
                deterministic=round(candidate.deterministic, 6),
                final=round(candidate.final, 6),
            ),
            features={k: float(v) for k, v in candidate.features.items()},
        )

    @staticmethod
    def _cache_item(candidate: Candidate) -> dict[str, Any]:
        record = candidate.record
        assert record is not None
        return {
            "chunk_id": candidate.chunk_id,
            "corpus_id": record.corpus_id,
            "path": record.path,
            "content_hash": record.content_hash,
            "scores": candidate.scores,
            "ranks": candidate.ranks,
            "lexical": candidate.scores.get("lexical"),
            "vector": candidate.scores.get("vector"),
            "fused_norm": candidate.fused_norm,
            "fused_rank": candidate.fused_rank,
            "reranker": candidate.reranker,
            "reranker_raw": candidate.reranker_raw,
            "reranker_rank": candidate.reranker_rank,
            "features": candidate.features,
            "deterministic": candidate.deterministic,
            "final": candidate.final,
        }

    def _from_cache(
        self,
        lookup: CacheLookup,
        request: RetrieveRequest,
        request_id: str,
        profile: Profile,
        query: BuiltQuery,
        corpora: dict[str, CorpusRecord],
        freshness_mode: str,
        degraded: list[str],
        timings: dict[str, float],
        started: float,
        trace: dict[str, Any],
    ) -> RetrieveResponse | None:
        assert lookup.result is not None
        items = lookup.result.get("selected", [])
        candidates = []
        for item in items:
            candidate = Candidate(
                chunk_id=item["chunk_id"],
                scores=item.get("scores", {}),
                ranks=item.get("ranks", {}),
                fused_norm=item.get("fused_norm", 0.0),
                fused_rank=item.get("fused_rank", 0),
                reranker=item.get("reranker"),
                reranker_raw=item.get("reranker_raw"),
                reranker_rank=item.get("reranker_rank"),
                features=item.get("features", {}),
                deterministic=item.get("deterministic", 0.0),
                final=item.get("final", 0.0),
            )
            candidates.append(candidate)
        self._hydrate(candidates)
        for candidate, item in zip(candidates, items, strict=True):
            if candidate.record is None or candidate.record.content_hash != item["content_hash"]:
                return None
        filtered = apply_hard_filters(
            candidates,
            corpora=corpora,
            profile=profile,
            workspace_state=request.workspace_state,
            freshness_mode=freshness_mode,
            verifier=self.verifier,
        )
        if filtered:
            return None
        for rank, candidate in enumerate(candidates, start=1):
            candidate.selected = True
            candidate.selection_reason = "cached"
            candidate.final_rank = rank
        chunks = [self._chunk_out(c, rank, corpora) for rank, c in enumerate(candidates, 1)]
        timings["total"] = _ms(started)
        cached_degraded = list(lookup.result.get("degraded_reasons", []))
        reasons = sorted(set(degraded + cached_degraded))
        response = RetrieveResponse(
            request_id=request_id,
            profile=profile.name,
            config_id=profile.config_id(),
            query=self._query_out(query, freshness_mode),
            cache=CacheInfo(
                status=lookup.status,
                key=lookup.cache_key,
                reason=lookup.reason,
                source_request_id=lookup.result.get("request_id"),
            ),
            snapshots={cid: self._snapshot(c) for cid, c in corpora.items()},
            degraded=bool(reasons),
            degraded_reasons=reasons,
            chunks=chunks,
            context=render_context(candidates, profile.name, request_id)
            if request.render_context
            else "",
            context_tokens=sum(c.token_count for c in chunks),
            stats={
                "candidates": len(candidates),
                "filtered": 0,
                "filtered_by_reason": {},
                "reranked": 0,
                "selected": len(candidates),
                "retrievers": {},
            },
            timings_ms=timings,
            candidates=[c.log_row(time.time()) for c in candidates]
            if request.include_candidates
            else None,
        )
        self.cache.store.cache_touch(lookup.cache_key)
        self._log(response, request, profile, query, candidates, 0, trace)
        return response

    @staticmethod
    def _snapshot(corpus: CorpusRecord) -> dict[str, Any]:
        return {
            "code_snapshot": corpus.code_snapshot,
            "docs_snapshot": corpus.docs_snapshot,
            "code_generation": corpus.code_generation,
            "docs_generation": corpus.docs_generation,
            "git_head": corpus.git_head,
            "last_sync_at": corpus.last_sync_at,
        }

    @staticmethod
    def _query_out(query: BuiltQuery, freshness_mode: str) -> dict[str, Any]:
        return {
            "rerank_text": query.rerank_text,
            "identifiers": query.identifiers,
            "paths": query.paths,
            "fingerprint": {
                "exact_hash": query.fingerprint.exact_hash,
                "normalized_hash": query.fingerprint.normalized_hash,
                "components": sorted(query.fingerprint.components),
            },
            "freshness_mode": freshness_mode,
        }

    def _log(
        self,
        response: RetrieveResponse,
        request: RetrieveRequest,
        profile: Profile,
        query: BuiltQuery,
        candidates: list[Candidate],
        n_reranked: int,
        trace: dict[str, Any],
    ) -> None:
        now = time.time()
        query_text = json.dumps(
            {"components": query.components, "rerank": query.rerank_text}, ensure_ascii=False
        )
        self.log.write(
            {
                "request_id": response.request_id,
                "ts": now,
                "profile": profile.name,
                "config_id": response.config_id,
                "corpora": request.corpora,
                "trace": trace,
                "query_text": query_text if self.settings.log_query_text else None,
                "query_hash": sha256_hex(query_text)[:24],
                "fingerprint": query.fingerprint.to_dict(),
                "cache_status": response.cache.status,
                "cache_source_request_id": response.cache.source_request_id,
                "degraded": response.degraded,
                "degraded_reasons": response.degraded_reasons,
                "n_candidates": len(candidates),
                "n_filtered": response.stats.get("filtered", 0),
                "n_reranked": n_reranked,
                "n_selected": len(response.chunks),
                "context_tokens": response.context_tokens,
                "timings_ms": response.timings_ms,
                "snapshots": response.snapshots,
                "embedder": self.embedder.model_id,
                "reranker": self.reranker.name,
            },
            [c.log_row(now) for c in candidates],
        )


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)
