"""Embedding backends.

* ``hashing`` — deterministic feature-hashing embedder with no model download. It is a lexical-ish
  baseline that keeps the vector path testable and reproducible; it is *not* a semantic model.
* ``openai`` — any OpenAI-compatible ``/v1/embeddings`` endpoint (llama.cpp server, TEI, Ollama,
  vLLM, hosted APIs). This is the production path.

Embeddings are cached by ``(model_id, content_hash)`` in the store, so unchanged chunks are never
re-embedded after an edit elsewhere in the corpus.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Protocol

import httpx
import numpy as np

from .text import content_terms, sha256_hex, words


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized: np.ndarray = (matrix / norms).astype(np.float32)
    return normalized


class HashingEmbedder:
    """Signed feature hashing over terms, identifier sub-tokens and character trigrams."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    @property
    def model_id(self) -> str:
        return f"hashing-v1-{self.dim}"

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        terms = content_terms(text, stem=True)
        features: list[tuple[str, float]] = [(f"t:{t}", 1.0) for t in terms]
        for token in {w.lower() for w in words(text) if len(w) >= 4}:
            padded = f"#{token}#"
            features.extend((f"c:{padded[i : i + 3]}", 0.35) for i in range(len(padded) - 2))
        for feature, weight in features:
            digest = int(sha256_hex(feature)[:16], 16)
            index = digest % self.dim
            sign = 1.0 if (digest >> 63) & 1 else -1.0
            vec[index] += sign * weight
        return vec

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _normalize(np.vstack([self._vector(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        vector: np.ndarray = self.embed_documents([text])[0]
        return vector


class OpenAICompatibleEmbedder:
    def __init__(
        self,
        url: str,
        model: str,
        api_key: str | None = None,
        query_prefix: str = "",
        passage_prefix: str = "",
        batch_size: int = 32,
        timeout_seconds: float = 30.0,
        max_chars: int = 8000,
        client: httpx.Client | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix
        self.batch_size = batch_size
        self.max_chars = max_chars
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(timeout=timeout_seconds, headers=headers)
        self._query_cache: OrderedDict[str, np.ndarray] = OrderedDict()

    @property
    def model_id(self) -> str:
        return (
            f"openai:{self.model}:{sha256_hex(self.query_prefix + '|' + self.passage_prefix)[:8]}"
        )

    def _post(self, inputs: list[str]) -> np.ndarray:
        endpoint = self.url if self.url.endswith("/embeddings") else f"{self.url}/embeddings"
        try:
            response = self._client.post(endpoint, json={"model": self.model, "input": inputs})
            response.raise_for_status()
            payload = response.json()
            data = sorted(payload["data"], key=lambda item: item.get("index", 0))
            matrix = np.asarray([item["embedding"] for item in data], dtype=np.float32)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"embedding request failed: {exc}") from exc
        if matrix.shape[0] != len(inputs):
            raise EmbeddingError("embedding response size mismatch")
        return _normalize(matrix)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        batches = []
        for offset in range(0, len(texts), self.batch_size):
            batch = [
                self.passage_prefix + t[: self.max_chars]
                for t in texts[offset : offset + self.batch_size]
            ]
            batches.append(self._post(batch))
        if not batches:
            return np.zeros((0, 0), dtype=np.float32)
        return np.vstack(batches)

    def embed_query(self, text: str) -> np.ndarray:
        key = sha256_hex(text)
        cached = self._query_cache.get(key)
        if cached is not None:
            self._query_cache.move_to_end(key)
            return cached
        vector: np.ndarray = self._post([self.query_prefix + text[: self.max_chars]])[0]
        self._query_cache[key] = vector
        if len(self._query_cache) > 512:
            self._query_cache.popitem(last=False)
        return vector
