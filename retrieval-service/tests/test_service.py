import pytest

import ahawr_retrieval.ranking as ranking_mod
import ahawr_retrieval.reranker as reranker_mod
from ahawr_retrieval.config import Settings
from ahawr_retrieval.models import RetrieveRequest
from ahawr_retrieval.reranker import RerankOutcome
from ahawr_retrieval.service import RetrievalService, build_reranker


def test_default_threshold_and_cutoff_are_off() -> None:
    """Defaults: the reranker threshold and the min final score cutoff are both 0.0 (off),
    so behaviour is unchanged when the flags are unset."""
    assert reranker_mod._RERANKER_THRESHOLD == 0.0
    assert ranking_mod._RANKING_MIN_FINAL_SCORE == 0.0


def test_reranker_default_mode_is_auto(settings: Settings) -> None:
    """The reranker is enabled through the existing RETRIEVAL_RERANKER flag; its default
    is 'auto', which with no URL set produces a NoopReranker."""
    assert build_reranker(settings).name == "none"


class AllBelowThresholdReranker:
    """Reranker whose backend answered, but every score fell below the threshold."""

    @property
    def name(self) -> str:
        return "http:test"

    def rerank(self, query: str, documents: list[tuple[str, str]]) -> RerankOutcome:
        return RerankOutcome(backend=self.name, scores={}, dropped=len(documents))


def test_all_below_threshold_keeps_fused_only_branch(settings: Settings, indexed) -> None:
    """When the backend answered but every score is below the threshold, the rerank is
    treated as done (note 'reranker_all_below_threshold'), and every candidate keeps the
    fused-only branch: final = fused * fused_norm + deterministic, no rerank credit."""
    service = RetrievalService(settings, reranker=AllBelowThresholdReranker())
    response = service.retrieve(
        RetrieveRequest(corpora=["ws"], query="compute_total", include_candidates=True)
    )
    assert "reranker_all_below_threshold" in response.notes
    fused_w = service.profiles["worker"].ranking.fused
    for c in response.candidates:
        assert c["reranker_score"] is None
        assert c["reranker_raw"] is None
        if c["filtered_reason"] is None:
            expected = fused_w * c["fused_score"] + c["deterministic_score"]
            assert c["final_score"] == pytest.approx(expected, abs=1e-5)
    service.close()
