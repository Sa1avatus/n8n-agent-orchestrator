import httpx
import pytest

import ahawr_retrieval.reranker as reranker_mod
from ahawr_retrieval.reranker import (
    _RERANKER_THRESHOLD,
    HttpReranker,
    LocalReranker,
)


def test_default_threshold_is_zero() -> None:
    """Default: threshold 0.0, so no filtering happens when the flag is unset."""
    assert _RERANKER_THRESHOLD == 0.0


def test_threshold_filters_low_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    """When threshold > 0, scores below it are dropped."""
    monkeypatch.setattr(reranker_mod, "_RERANKER_THRESHOLD", 0.5)
    # scorer returns raw logit scores; sigmoid(5.0) ~ 0.99 (kept), sigmoid(-5.0) ~ 0.007 (dropped)
    reranker = LocalReranker(scorer=lambda q, docs: [5.0, -5.0])
    outcome = reranker.rerank("q", [("a", "x"), ("b", "y")])
    assert set(outcome.scores) == {"a"}


def test_threshold_zero_keeps_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """When threshold is 0.0 (default), no scores are filtered."""
    monkeypatch.setattr(reranker_mod, "_RERANKER_THRESHOLD", 0.0)
    reranker = LocalReranker(scorer=lambda q, docs: [5.0, -5.0])
    outcome = reranker.rerank("q", [("a", "x"), ("b", "y")])
    assert set(outcome.scores) == {"a", "b"}


def test_threshold_all_below_drops_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """When every score is below the threshold, the result is empty."""
    monkeypatch.setattr(reranker_mod, "_RERANKER_THRESHOLD", 0.5)
    reranker = LocalReranker(scorer=lambda q, docs: [-5.0, -3.0])
    outcome = reranker.rerank("q", [("a", "x"), ("b", "y")])
    assert outcome.scores == {}


def test_http_reranker_threshold_filters_low_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HttpReranker applies the same threshold filter over a mocked transport."""
    monkeypatch.setattr(reranker_mod, "_RERANKER_THRESHOLD", 0.5)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": "a", "score": 5.0, "normalized_score": 0.99},
                    {"id": "b", "score": -5.0, "normalized_score": 0.007},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    reranker = HttpReranker("http://reranker:8200", client=client)
    outcome = reranker.rerank("q", [("a", "x"), ("b", "y")])
    assert set(outcome.scores) == {"a"}
