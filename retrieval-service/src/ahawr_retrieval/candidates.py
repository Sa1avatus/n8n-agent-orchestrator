"""Candidate records and weighted reciprocal-rank-fusion merge of retriever outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .store import ChunkRecord, FileRecord

RETRIEVERS = ("lexical", "vector", "symbol")


@dataclass
class Candidate:
    chunk_id: str
    scores: dict[str, float] = field(default_factory=dict)  # retriever -> raw score
    ranks: dict[str, int] = field(default_factory=dict)  # retriever -> 1-based rank
    fused: float = 0.0
    fused_norm: float = 0.0
    fused_rank: int = 0
    record: ChunkRecord | None = None
    file: FileRecord | None = None
    freshness: str = "indexed"
    filtered_reason: str | None = None
    reranker: float | None = None
    reranker_raw: float | None = None
    reranker_rank: int | None = None
    features: dict[str, float] = field(default_factory=dict)
    deterministic: float = 0.0
    final: float = 0.0
    final_rank: int | None = None
    selected: bool = False
    selection_reason: str | None = None

    def log_row(self, now: float) -> dict[str, Any]:
        record = self.record
        return {
            "chunk_id": self.chunk_id,
            "corpus_id": record.corpus_id if record else None,
            "path": record.path if record else None,
            "source_type": record.source_type if record else None,
            "symbol": record.symbol if record else None,
            "section": record.section if record else None,
            "content_hash": record.content_hash if record else None,
            "token_count": record.token_count if record else None,
            "chunk_age_seconds": round(now - record.indexed_at, 1) if record else None,
            "lexical_score": self.scores.get("lexical"),
            "lexical_rank": self.ranks.get("lexical"),
            "vector_score": self.scores.get("vector"),
            "vector_rank": self.ranks.get("vector"),
            "symbol_score": self.scores.get("symbol"),
            "symbol_rank": self.ranks.get("symbol"),
            "fused_score": round(self.fused_norm, 6),
            "fused_rank": self.fused_rank,
            "reranker_score": self.reranker,
            "reranker_raw": self.reranker_raw,
            "reranker_rank": self.reranker_rank,
            "exact_symbol": self.features.get("exact_symbol"),
            "path_mentioned": self.features.get("path_mentioned"),
            "scope_match": self.features.get("scope_match"),
            "test_file": self.features.get("test_file"),
            "source_prior": self.features.get("source_prior"),
            "deterministic_score": round(self.deterministic, 6),
            "final_score": round(self.final, 6),
            "final_rank": self.final_rank,
            "freshness": self.freshness,
            "filtered_reason": self.filtered_reason,
            "selected": self.selected,
            "selection_reason": self.selection_reason,
        }


def fuse(
    results: dict[str, list[tuple[str, float]]],
    weights: dict[str, float],
    rrf_k: int,
) -> list[Candidate]:
    """Weighted RRF: ``sum_r w_r / (k + rank_r)``, normalized by the best fused score."""
    candidates: dict[str, Candidate] = {}
    for retriever, items in results.items():
        weight = weights.get(retriever, 1.0)
        for rank, (chunk_id, score) in enumerate(items, start=1):
            candidate = candidates.setdefault(chunk_id, Candidate(chunk_id))
            if retriever in candidate.ranks:
                continue
            candidate.scores[retriever] = round(float(score), 6)
            candidate.ranks[retriever] = rank
            candidate.fused += weight / (rrf_k + rank)
    ordered = sorted(
        candidates.values(),
        key=lambda c: (-c.fused, min(c.ranks.values()), c.chunk_id),
    )
    best = ordered[0].fused if ordered else 1.0
    for rank, candidate in enumerate(ordered, start=1):
        candidate.fused_rank = rank
        candidate.fused_norm = candidate.fused / best if best else 0.0
    return ordered
