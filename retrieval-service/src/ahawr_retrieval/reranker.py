"""Text-only cross-encoder reranking client.

The reranker scores only the relevance of ``query + chunk text`` pairs. It receives no freshness,
path, symbol or source-type features: hard constraints are enforced by deterministic filters
before reranking, and metadata/lexical/exact-match signals are combined afterwards by the
deterministic ranking layer (ranking.py).

Backends:
- ``HttpReranker``: the standalone ``reranker-service`` (``POST /v1/rerank``).
- ``LocalReranker``: a small ONNX cross-encoder run in-process on the CPU (onnxruntime, no GPU),
  so the stack can rerank on its own. The container bakes the model into the image. It is opt-in
  (``RETRIEVAL_RERANKER=local``): on the gold set the small CPU models ranked below the
  deterministic ranking without a reranker (eval/results/2026-09-28-local-models.md).
Any failure degrades to the fused retrieval order and is reported in the response.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import httpx


@dataclass
class RerankOutcome:
    scores: dict[str, tuple[float, float]] = field(default_factory=dict)  # id -> (raw, normalized)
    latency_ms: float = 0.0
    backend: str = "none"
    degraded_reason: str | None = None


class Reranker(Protocol):
    @property
    def name(self) -> str: ...

    def rerank(self, query: str, documents: list[tuple[str, str]]) -> RerankOutcome: ...


class NoopReranker:
    @property
    def name(self) -> str:
        return "none"

    def rerank(self, query: str, documents: list[tuple[str, str]]) -> RerankOutcome:
        return RerankOutcome(backend="none", degraded_reason="reranker_not_configured")


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1.0 + exp)


class HttpReranker:
    """Client for reranker-service ``/v1/rerank`` (bge-reranker-v2-m3 by default)."""

    def __init__(
        self,
        url: str,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        max_documents: int = 100,
        max_chars: int = 4000,
        client: httpx.Client | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.max_documents = max_documents
        self.max_chars = max_chars
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(timeout=timeout_seconds, headers=headers)

    @property
    def name(self) -> str:
        return f"http:{self.url}"

    def rerank(self, query: str, documents: list[tuple[str, str]]) -> RerankOutcome:
        outcome = RerankOutcome(backend=self.name)
        if not documents:
            return outcome
        started = time.perf_counter()
        endpoint = self.url if self.url.endswith("/v1/rerank") else f"{self.url}/v1/rerank"
        try:
            for offset in range(0, len(documents), self.max_documents):
                batch = documents[offset : offset + self.max_documents]
                response = self._client.post(
                    endpoint,
                    json={
                        "query": query[:8000],
                        "documents": [
                            {"id": doc_id, "text": text[: self.max_chars] or " "}
                            for doc_id, text in batch
                        ],
                        "top_n": len(batch),
                        "return_documents": False,
                        "truncate": True,
                    },
                )
                response.raise_for_status()
                for result in response.json()["results"]:
                    raw = float(result["score"])
                    normalized = result.get("normalized_score")
                    outcome.scores[str(result["id"])] = (
                        raw,
                        float(normalized) if normalized is not None else _sigmoid(raw),
                    )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            outcome.scores = {}
            outcome.degraded_reason = f"reranker_unavailable:{type(exc).__name__}"
        outcome.latency_ms = (time.perf_counter() - started) * 1000
        return outcome


DEFAULT_LOCAL_MODEL = "jinaai/jina-reranker-v1-tiny-en"

# (query, documents) -> raw relevance scores, one per document, in order.
ScoreFn = Callable[[str, Sequence[str]], Iterable[float]]


def _fastembed_scorer(
    model: str, cache_dir: str | None, threads: int | None, batch_size: int
) -> ScoreFn:
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
    except ImportError as exc:  # the [local-rerank] extra is not installed
        raise RuntimeError("fastembed is not installed (pip install '.[local-rerank]')") from exc
    # CPU only: onnxruntime without CUDA, and the CPU provider is pinned explicitly.
    encoder = TextCrossEncoder(
        model_name=model,
        cache_dir=cache_dir,
        threads=threads,
        providers=["CPUExecutionProvider"],
        cuda=False,
    )

    def score(query: str, documents: Sequence[str]) -> Iterable[float]:
        return [float(s) for s in encoder.rerank(query, documents, batch_size=batch_size)]

    return score


class LocalReranker:
    """In-process ONNX cross-encoder on the CPU (fastembed); loaded on first use."""

    def __init__(
        self,
        model: str = DEFAULT_LOCAL_MODEL,
        cache_dir: str | None = None,
        threads: int | None = None,
        max_chars: int = 2000,
        batch_size: int = 16,
        scorer: ScoreFn | None = None,
    ) -> None:
        self.model = model
        self.cache_dir = cache_dir
        self.threads = threads
        self.max_chars = max_chars
        self.batch_size = batch_size
        self._scorer = scorer
        self._load_error: str | None = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return f"local:{self.model}"

    def _get_scorer(self) -> ScoreFn | None:
        with self._lock:
            if self._scorer is None and self._load_error is None:
                try:
                    self._scorer = _fastembed_scorer(
                        self.model, self.cache_dir, self.threads, self.batch_size
                    )
                except Exception as exc:  # missing package or model files: stay degraded
                    self._load_error = type(exc).__name__
            return self._scorer

    def rerank(self, query: str, documents: list[tuple[str, str]]) -> RerankOutcome:
        outcome = RerankOutcome(backend=self.name)
        if not documents:
            return outcome
        started = time.perf_counter()
        scorer = self._get_scorer()
        if scorer is None:
            outcome.degraded_reason = f"reranker_unavailable:{self._load_error}"
            return outcome
        try:
            texts = [text[: self.max_chars] or " " for _, text in documents]
            raw_scores = [float(s) for s in scorer(query[:8000], texts)]
            if len(raw_scores) != len(documents):
                raise ValueError("score count does not match document count")
            for (doc_id, _), raw in zip(documents, raw_scores, strict=True):
                outcome.scores[doc_id] = (raw, _sigmoid(raw))
        except Exception as exc:
            outcome.scores = {}
            outcome.degraded_reason = f"reranker_unavailable:{type(exc).__name__}"
        outcome.latency_ms = (time.perf_counter() - started) * 1000
        return outcome
