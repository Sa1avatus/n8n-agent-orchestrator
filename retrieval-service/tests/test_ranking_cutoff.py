import pytest

import ahawr_retrieval.ranking as ranking_mod
from ahawr_retrieval.profiles import Profile
from ahawr_retrieval.ranking import (
    rank_candidates,
)
from tests.test_ranking import make_candidate, make_query


def test_default_min_final_score_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default flag off: _RANKING_MIN_FINAL_SCORE == 0.0, no filtering."""
    monkeypatch.setattr(ranking_mod, "_RANKING_MIN_FINAL_SCORE", 0.0)
    profile = Profile(name="worker")
    candidates = [
        make_candidate("ch1", fused_norm=0.9, fused_rank=1),
        make_candidate("ch2", fused_norm=0.1, fused_rank=2),
    ]
    result = rank_candidates(candidates, make_query(), profile, reranked=False)
    assert len(result) == 2  # no filtering with 0.0


def test_min_final_score_cutoff_filters_weak(monkeypatch: pytest.MonkeyPatch) -> None:
    """When set > 0, candidates below the cutoff are dropped; empty result allowed."""
    monkeypatch.setattr(ranking_mod, "_RANKING_MIN_FINAL_SCORE", 0.6)
    profile = Profile(name="worker")
    # ch1 has high final (~0.82 + prior ~0.02 = ~0.84), ch2 has low final (~0.12 + 0.02 = ~0.14)
    candidates = [
        make_candidate("ch1", fused_norm=0.9, fused_rank=1),
        make_candidate("ch2", fused_norm=0.1, fused_rank=2),
    ]
    result = rank_candidates(candidates, make_query(), profile, reranked=False)
    ids = [c.chunk_id for c in result]
    assert "ch1" in ids
    assert "ch2" not in ids


def test_min_final_score_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """When all candidates fall below cutoff, result is empty (not an error)."""
    monkeypatch.setattr(ranking_mod, "_RANKING_MIN_FINAL_SCORE", 1.0)
    profile = Profile(name="worker")
    candidates = [
        make_candidate("ch1", fused_norm=0.9, fused_rank=1),
    ]
    result = rank_candidates(candidates, make_query(), profile, reranked=False)
    assert result == []
