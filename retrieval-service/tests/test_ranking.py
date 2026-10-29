import pytest

import ahawr_retrieval.ranking as ranking_mod
from ahawr_retrieval.candidates import Candidate
from ahawr_retrieval.profiles import Profile
from ahawr_retrieval.query_builder import BuiltQuery
from ahawr_retrieval.ranking import (
    _RANKING_USE_RRF,
    rank_candidates,
)


def make_candidate(chunk_id: str, fused_norm: float = 0.8, fused_rank: int = 1) -> Candidate:
    from ahawr_retrieval.store import ChunkRecord

    record = ChunkRecord(
        id=1,
        chunk_id=chunk_id,
        corpus_id="ws",
        path=chunk_id,
        source_type="code",
        anchor=chunk_id,
        symbol=chunk_id,
        symbol_kind="function",
        section=None,
        language="python",
        start_line=1,
        end_line=5,
        content="def x(): pass",
        content_hash="h",
        file_hash="fh",
        version="v",
        snapshot_id=None,
        token_count=10,
        status="active",
        status_reason=None,
        generation=1,
        indexed_at=0.0,
        updated_at=0.0,
    )
    candidate = Candidate(chunk_id=chunk_id, record=record)
    candidate.fused_norm = fused_norm
    candidate.fused_rank = fused_rank
    return candidate


def make_query() -> BuiltQuery:
    return BuiltQuery(
        lexical_text="x",
        vector_text="x",
        rerank_text="x",
        identifiers=[],
        paths=[],
        scope=[],
        changed_paths=[],
    )


def test_default_output_unchanged_without_flags() -> None:
    """When all flags are off (default), output must match the current behavior exactly."""
    assert not _RANKING_USE_RRF
    candidates = [
        make_candidate("ch1", fused_norm=0.9, fused_rank=1),
        make_candidate("ch2", fused_norm=0.7, fused_rank=2),
    ]
    profile = Profile(name="worker")
    weights = profile.ranking
    query = make_query()
    result = rank_candidates(candidates, query, profile, reranked=False)
    assert [c.chunk_id for c in result] == ["ch1", "ch2"]
    # Original formula, flag off: relevance = (rerank + fused) * fused_norm + deterministic.
    by_id = {c.chunk_id: c for c in result}
    base = weights.rerank + weights.fused
    prior = weights.source_prior["code"]
    assert by_id["ch1"].final == pytest.approx(base * 0.9 + prior)
    assert by_id["ch2"].final == pytest.approx(base * 0.7 + prior)
    assert by_id["ch1"].final_rank == 1
    assert by_id["ch2"].final_rank == 2


def test_rrf_weights_change_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """A larger vector weight flips the order of two candidates under RRF mode."""
    monkeypatch.setattr(ranking_mod, "_RANKING_USE_RRF", True)
    monkeypatch.setattr(ranking_mod, "_RANKING_RRF_K", 60)
    monkeypatch.setattr(ranking_mod, "_RANKING_LEXICAL_WEIGHT", 1.0)
    monkeypatch.setattr(ranking_mod, "_RANKING_VECTOR_WEIGHT", 1.0)
    profile = Profile(name="worker")
    query = make_query()

    def run(vector_weight: float) -> list[str]:
        monkeypatch.setattr(ranking_mod, "_RANKING_VECTOR_WEIGHT", vector_weight)
        # ch_a: lexical rank 1, vector rank 4 -> 1/61 + w/64
        # ch_b: lexical rank 3, vector rank 2 -> 1/63 + w/62
        # at w = 1.0 ch_a leads; at w = 3.0 ch_b leads.
        a = make_candidate("ch_a", fused_norm=0.8, fused_rank=1)
        a.ranks = {"lexical": 1, "vector": 4}
        b = make_candidate("ch_b", fused_norm=0.6, fused_rank=2)
        b.ranks = {"lexical": 3, "vector": 2}
        result = rank_candidates([a, b], query, profile, reranked=False)
        return [c.chunk_id for c in result]

    assert run(1.0) == ["ch_a", "ch_b"]
    assert run(3.0) == ["ch_b", "ch_a"]


def test_rrf_k_changes_final_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    """Different RRF k values change the final scores while the order stays the same."""
    monkeypatch.setattr(ranking_mod, "_RANKING_USE_RRF", True)
    monkeypatch.setattr(ranking_mod, "_RANKING_LEXICAL_WEIGHT", 1.0)
    monkeypatch.setattr(ranking_mod, "_RANKING_VECTOR_WEIGHT", 1.0)
    profile = Profile(name="worker")
    query = make_query()

    def run(k: int) -> dict[str, float]:
        monkeypatch.setattr(ranking_mod, "_RANKING_RRF_K", k)
        a = make_candidate("ch_a", fused_norm=0.5, fused_rank=2)
        a.ranks = {"lexical": 1, "vector": 3}
        b = make_candidate("ch_b", fused_norm=0.6, fused_rank=1)
        b.ranks = {"lexical": 2, "vector": 2}
        result = rank_candidates([a, b], query, profile, reranked=False)
        return {c.chunk_id: c.final for c in result}

    prior = profile.ranking.source_prior["code"]
    scores_k10 = run(10)
    scores_k100 = run(100)
    assert scores_k10["ch_a"] == pytest.approx(1 / 11 + 1 / 13 + prior)
    assert scores_k100["ch_a"] == pytest.approx(1 / 101 + 1 / 103 + prior)
    assert abs(scores_k10["ch_a"] - scores_k100["ch_a"]) > 0.01
    assert list(scores_k10) == list(scores_k100)


def test_max_fragments_per_file_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A per-file fragment cap keeps at most N fragments per path when set (> 0)."""
    monkeypatch.setattr(ranking_mod, "_RANKING_MAX_FRAGMENTS_PER_FILE", 2)
    candidates = [
        make_candidate("ch1", fused_norm=0.9, fused_rank=1),
        make_candidate("ch2", fused_norm=0.8, fused_rank=2),
        make_candidate("ch3", fused_norm=0.7, fused_rank=3),
    ]
    for c in candidates:
        assert c.record is not None
        c.record.path = "same/file.py"
    profile = Profile(name="worker")
    query = make_query()
    result = rank_candidates(candidates, query, profile, reranked=False)
    assert len(result) == 2
    assert [c.chunk_id for c in result] == ["ch1", "ch2"]


def test_per_file_cap_unset_no_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the cap is unset (0), no cap is applied."""
    monkeypatch.setattr(ranking_mod, "_RANKING_MAX_FRAGMENTS_PER_FILE", 0)
    candidates = [make_candidate("ch1"), make_candidate("ch2"), make_candidate("ch3")]
    profile = Profile(name="worker")
    query = make_query()
    result = rank_candidates(candidates, query, profile, reranked=False)
    assert len(result) == 3
