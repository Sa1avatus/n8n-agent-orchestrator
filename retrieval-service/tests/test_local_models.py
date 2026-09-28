"""Built-in CPU models (local reranker and embedder), with the ONNX models replaced by fakes."""

from collections.abc import Sequence
from dataclasses import replace

import numpy as np
import pytest

from ahawr_retrieval.config import Settings
from ahawr_retrieval.embeddings import EmbeddingError, LocalEmbedder
from ahawr_retrieval.reranker import (
    HttpReranker,
    LocalReranker,
    NoopReranker,
)
from ahawr_retrieval.service import build_embedder, build_reranker


def keyword_scorer(query: str, documents: Sequence[str]) -> list[float]:
    return [5.0 if "total" in doc else -5.0 for doc in documents]


def test_local_reranker_scores_and_normalizes() -> None:
    seen: list[str] = []

    def scorer(query: str, documents: Sequence[str]) -> list[float]:
        seen.extend(documents)
        return keyword_scorer(query, documents)

    reranker = LocalReranker("m", max_chars=10, scorer=scorer)
    outcome = reranker.rerank("q", [("a", "total: compute here"), ("b", "")])
    assert outcome.backend == "local:m"
    assert outcome.degraded_reason is None
    assert outcome.scores["a"][1] > 0.99 and outcome.scores["b"][1] < 0.01
    assert seen == ["total: com", " "]  # truncated to max_chars, empty text padded


def test_local_reranker_degrades_on_scorer_failure_and_missing_model() -> None:
    def broken(query: str, documents: Sequence[str]) -> list[float]:
        return [1.0]  # wrong length

    outcome = LocalReranker("m", scorer=broken).rerank("q", [("a", "x"), ("b", "y")])
    assert outcome.scores == {}
    assert outcome.degraded_reason == "reranker_unavailable:ValueError"
    assert LocalReranker("m", scorer=broken).rerank("q", []).degraded_reason is None

    missing = LocalReranker("no/such-model", cache_dir="/nonexistent")
    missing._load_error = "RuntimeError"  # as if fastembed or the model files were absent
    assert missing.rerank("q", [("a", "x")]).degraded_reason == "reranker_unavailable:RuntimeError"


def test_build_reranker_modes(settings: Settings) -> None:
    with_url = replace(settings, reranker_url="http://reranker:8200")
    assert isinstance(build_reranker(replace(settings, reranker="none")), NoopReranker)
    assert isinstance(build_reranker(replace(with_url, reranker="none")), NoopReranker)
    assert isinstance(build_reranker(replace(settings, reranker="local")), LocalReranker)
    assert isinstance(build_reranker(replace(with_url, reranker="local")), LocalReranker)
    # auto/http: the reranker-service when configured, otherwise no reranking
    assert isinstance(build_reranker(replace(settings, reranker="auto")), NoopReranker)
    assert isinstance(build_reranker(replace(settings, reranker="http")), NoopReranker)
    assert isinstance(build_reranker(replace(with_url, reranker="auto")), HttpReranker)
    assert isinstance(build_reranker(replace(with_url, reranker="http")), HttpReranker)
    with pytest.raises(ValueError):
        build_reranker(replace(settings, reranker="gpu"))


def test_reranker_settings_from_env() -> None:
    settings = Settings.from_env(
        {
            "RETRIEVAL_RERANKER": " Local ",
            "RETRIEVAL_RERANKER_MODEL": "Xenova/ms-marco-MiniLM-L-6-v2",
            "RETRIEVAL_RERANKER_MODEL_DIR": "/models",
            "RETRIEVAL_RERANKER_THREADS": "2",
            "RETRIEVAL_RERANKER_LOCAL_MAX_CHARS": "1500",
        }
    )
    assert settings.reranker == "local"
    assert settings.reranker_model == "Xenova/ms-marco-MiniLM-L-6-v2"
    assert settings.reranker_model_dir == "/models"
    assert settings.reranker_threads == 2
    assert settings.reranker_local_max_chars == 1500
    defaults = Settings.from_env({})
    assert defaults.reranker == "auto" and defaults.reranker_threads is None


def test_local_embedder_prefixes_normalizes_and_caches_queries() -> None:
    calls: list[list[str]] = []

    def embed(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[3.0, 4.0] for _ in texts]

    embedder = LocalEmbedder("m", embed_fn=embed)
    docs = embedder.embed_documents(["alpha", "beta"])
    assert calls[0] == ["passage: alpha", "passage: beta"]
    assert np.allclose(np.linalg.norm(docs, axis=1), 1.0)
    first = embedder.embed_query("find alpha")
    second = embedder.embed_query("find alpha")
    assert calls[1:] == [["query: find alpha"]]
    assert np.array_equal(first, second)
    assert embedder.embed_documents([]).shape == (0, 0)
    assert embedder.model_id.startswith("local:m:")


def test_local_embedder_errors_are_typed() -> None:
    def broken(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("onnx failed")

    with pytest.raises(EmbeddingError):
        LocalEmbedder("m", embed_fn=broken).embed_documents(["x"])
    with pytest.raises(EmbeddingError):
        LocalEmbedder("m", embed_fn=lambda texts: [[1.0]]).embed_documents(["x", "y"])


def test_build_embedder_local(settings: Settings) -> None:
    embedder = build_embedder(replace(settings, embedder="local", reranker_model_dir="/models"))
    assert isinstance(embedder, LocalEmbedder)
    assert embedder.model == "intfloat/multilingual-e5-small"
    assert (embedder.query_prefix, embedder.passage_prefix) == ("query: ", "passage: ")
    assert embedder.cache_dir == "/models"
