"""Text-only cross-encoder reranking client.

The reranker scores only the relevance of ``query + chunk text`` pairs. It receives no freshness,
path, symbol or source-type features: hard constraints are enforced by deterministic filters
before reranking, and metadata/lexical/exact-match signals are combined afterwards by the
deterministic ranking layer (ranking.py).

The HTTP backend targets the standalone ``reranker-service`` (``POST /v1/rerank``). Any failure
degrades to the fused retrieval order and is reported in the response.
"""

from __future__ import annotations

import math
import time
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
