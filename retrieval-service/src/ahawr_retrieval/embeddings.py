"""Embedding backends.

* ``hashing`` — deterministic feature-hashing embedder with no model download. It is a lexical-ish
  baseline that keeps the vector path testable and reproducible; it is *not* a semantic model.
* ``local`` — a small ONNX embedding model run in-process on the CPU (fastembed/onnxruntime, no
  GPU); multilingual-e5-small by default. The container bakes the model into the image, so the
  stack gets semantic vectors without any external service.
* ``openai`` — any OpenAI-compatible ``/v1/embeddings`` endpoint (llama.cpp server, TEI, Ollama,
  vLLM, hosted APIs).

Embeddings are cached by ``(model_id, content_hash)`` in the store, so unchanged chunks are never
re-embedded after an edit elsewhere in the corpus.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from typing import Any, Protocol

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


DEFAULT_LOCAL_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"

# Models fastembed does not list itself: name -> (Hugging Face repo with ONNX weights, file, dim).
_CUSTOM_MODELS: dict[str, tuple[str, str, int]] = {
    "intfloat/multilingual-e5-small": ("Xenova/multilingual-e5-small", "onnx/model.onnx", 384),
}

# texts -> vectors, one per text, in order.
EmbedFn = Callable[[list[str]], Iterable[Any]]


def _fastembed_embedder(
    model: str, cache_dir: str | None, threads: int | None, batch_size: int
) -> EmbedFn:
    try:
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType
    except ImportError as exc:  # the [local-rerank] extra is not installed
        raise EmbeddingError("fastembed is not installed (pip install '.[local-rerank]')") from exc
    if model in _CUSTOM_MODELS:
        known = {m["model"] for m in TextEmbedding.list_supported_models()}
        if model not in known:
            repo, model_file, dim = _CUSTOM_MODELS[model]
            TextEmbedding.add_custom_model(
                model=model,
                pooling=PoolingType.MEAN,
                normalization=True,
                sources=ModelSource(hf=repo),
                dim=dim,
                model_file=model_file,
            )
    # CPU only: onnxruntime without CUDA, and the CPU provider is pinned explicitly.
    encoder = TextEmbedding(
        model_name=model,
        cache_dir=cache_dir,
        threads=threads,
        providers=["CPUExecutionProvider"],
        cuda=False,
    )

    def embed(texts: list[str]) -> Iterable[Any]:
        vectors: Iterable[Any] = encoder.embed(texts, batch_size=batch_size)
        return vectors

    return embed


class LocalEmbedder:
    """In-process ONNX embedding model on the CPU (fastembed); loaded on first use."""

    def __init__(
        self,
        model: str = DEFAULT_LOCAL_EMBEDDING_MODEL,
        cache_dir: str | None = None,
        threads: int | None = None,
        query_prefix: str = "query: ",
        passage_prefix: str = "passage: ",
        batch_size: int = 32,
        max_chars: int = 2000,
        embed_fn: EmbedFn | None = None,
    ) -> None:
        self.model = model
        self.cache_dir = cache_dir
        self.threads = threads
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix
        self.batch_size = batch_size
        self.max_chars = max_chars
        self._embed_fn = embed_fn
        self._lock = threading.Lock()
        self._query_cache: OrderedDict[str, np.ndarray] = OrderedDict()

    @property
    def model_id(self) -> str:
        return f"local:{self.model}:{sha256_hex(self.query_prefix + '|' + self.passage_prefix)[:8]}"

    def _embed(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            if self._embed_fn is None:
                self._embed_fn = _fastembed_embedder(
                    self.model, self.cache_dir, self.threads, self.batch_size
                )
            try:
                matrix = np.asarray(list(self._embed_fn(texts)), dtype=np.float32)
            except Exception as exc:
                raise EmbeddingError(f"local embedding failed: {exc}") from exc
        if matrix.ndim != 2 or matrix.shape[0] != len(texts):
            raise EmbeddingError("local embedding size mismatch")
        return _normalize(matrix)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        return self._embed([self.passage_prefix + t[: self.max_chars] for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        key = sha256_hex(text)
        cached = self._query_cache.get(key)
        if cached is not None:
            self._query_cache.move_to_end(key)
            return cached
        vector: np.ndarray = self._embed([self.query_prefix + text[: self.max_chars]])[0]
        self._query_cache[key] = vector
        if len(self._query_cache) > 512:
            self._query_cache.popitem(last=False)
        return vector
