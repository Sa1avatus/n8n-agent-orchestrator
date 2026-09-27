import json
from dataclasses import replace
from pathlib import Path

import httpx
import numpy as np
import pytest
import respx

from ahawr_retrieval.config import Settings
from ahawr_retrieval.embeddings import EmbeddingError, HashingEmbedder, OpenAICompatibleEmbedder
from ahawr_retrieval.models import IndexRequest, RetrieveRequest
from ahawr_retrieval.reranker import NoopReranker
from ahawr_retrieval.service import RetrievalService, build_embedder


def test_hashing_embedder_is_deterministic_and_normalized() -> None:
    embedder = HashingEmbedder(64)
    a = embedder.embed_documents(["parse invoice totals", "retry payment policy"])
    b = embedder.embed_documents(["parse invoice totals", "retry payment policy"])
    assert np.allclose(a, b)
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0)
    q = embedder.embed_query("invoice totals parsing")
    assert float(a[0] @ q) > float(a[1] @ q)


@respx.mock
def test_openai_compatible_embedder_uses_prefixes_and_caches_queries() -> None:
    seen: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        seen.append(inputs)
        data = [{"index": i, "embedding": [1.0, float(i), 0.0]} for i in range(len(inputs))]
        return httpx.Response(200, json={"data": list(reversed(data))})

    respx.post("http://emb:8080/v1/embeddings").mock(side_effect=handler)
    embedder = OpenAICompatibleEmbedder(
        "http://emb:8080/v1",
        "e5",
        query_prefix="query: ",
        passage_prefix="passage: ",
        batch_size=2,
    )
    matrix = embedder.embed_documents(["a", "b", "c"])
    assert matrix.shape == (3, 3)
    assert seen[0] == ["passage: a", "passage: b"] and seen[1] == ["passage: c"]
    embedder.embed_query("q")
    embedder.embed_query("q")
    assert seen[-1] == ["query: q"] and len(seen) == 3
    assert embedder.model_id.startswith("openai:e5:")


@respx.mock
def test_openai_embedder_errors_are_typed() -> None:
    respx.post("http://emb:8080/embeddings").mock(return_value=httpx.Response(500))
    with pytest.raises(EmbeddingError):
        OpenAICompatibleEmbedder("http://emb:8080", "m").embed_query("x")


def test_build_embedder_validation(settings: Settings) -> None:
    with pytest.raises(ValueError):
        build_embedder(replace(settings, embedder="openai"))
    with pytest.raises(ValueError):
        build_embedder(replace(settings, embedder="unknown"))


@respx.mock
def test_embedding_outage_degrades_to_lexical_and_backfills(
    settings: Settings, workspace: Path
) -> None:
    route = respx.post("http://emb:8080/embeddings")
    route.mock(return_value=httpx.Response(503))
    embedder = OpenAICompatibleEmbedder("http://emb:8080", "m")
    service = RetrievalService(settings, embedder=embedder, reranker=NoopReranker())
    try:
        result = service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        assert "embedding_unavailable" in result.degraded_reasons
        assert result.embeddings["pending"] > 0
        response = service.retrieve(RetrieveRequest(corpora=["ws"], query="compute_total"))
        assert "vector_unavailable" in response.degraded_reasons
        assert response.chunks[0].symbol == "compute_total"  # lexical + symbol still work

        def ok(request: httpx.Request) -> httpx.Response:
            inputs = json.loads(request.content)["input"]
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": i, "embedding": [1.0, 0.5, float(len(t) % 7)]}
                        for i, t in enumerate(inputs)
                    ]
                },
            )

        route.mock(side_effect=ok)
        assert service.indexer.backfill_embeddings("ws") > 0
        after = service.retrieve(
            RetrieveRequest(corpora=["ws"], query="compute_total", cache="bypass")
        )
        assert "vector_unavailable" not in after.degraded_reasons
        assert after.stats["retrievers"]["vector"] > 0
    finally:
        service.close()
