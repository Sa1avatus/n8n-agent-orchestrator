import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from ahawr_retrieval.config import Settings
from ahawr_retrieval.models import IndexRequest, RetrieveRequest
from ahawr_retrieval.service import RetrievalService

from .conftest import make_service

RAG = "http://rag:8100"


class FakeRag:
    """Minimal in-memory stand-in for the rag-platform /v1 service API."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        self.search_calls: list[dict[str, Any]] = []
        self.stale: dict[str, str] = {}

    def batch(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer rag-key"
        assert request.headers["X-Owner-User-Id"] == "00000000-0000-0000-0000-000000000001"
        out = []
        for doc in json.loads(request.content)["documents"]:
            doc_id = f"doc-{doc['external_document_id']}"
            self.documents[doc_id] = doc
            out.append(
                {
                    "id": doc_id,
                    "external_document_id": doc["external_document_id"],
                    "version": doc["version"],
                    "status": "queued",
                    "content_hash": "x",
                }
            )
        return httpx.Response(202, json=out)

    def delete(self, request: httpx.Request) -> httpx.Response:
        doc_id = request.url.path.rsplit("/", 1)[-1]
        self.deleted.append(doc_id)
        self.documents.pop(doc_id, None)
        return httpx.Response(204)

    def search(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.search_calls.append(payload)
        assert payload["use_reranker"] is False
        corpus = payload["filters"]["ahawr_corpus"]
        terms = {t for t in payload["query"].lower().split() if len(t) > 3}
        results = []
        for doc in self.documents.values():
            meta = doc["metadata"]
            if meta["ahawr_corpus"] != corpus:
                continue
            overlap = sum(t in doc["content"].lower() for t in terms)
            if overlap:
                meta = {
                    **meta,
                    "ahawr_content_hash": self.stale.get(
                        meta["ahawr_chunk_id"], meta["ahawr_content_hash"]
                    ),
                }
                results.append(
                    {
                        "external_document_id": doc["external_document_id"],
                        "metadata": meta,
                        "bm25_score": float(overlap) if payload["mode"] == "lexical" else None,
                        "vector_score": overlap / 10 if payload["mode"] == "dense" else None,
                        "fusion_score": overlap / 100,
                    }
                )
        results.sort(key=lambda r: -(r["bm25_score"] or r["vector_score"] or 0))
        return httpx.Response(200, json={"request_id": "r", "results": results[:50]})


@pytest.fixture
def rag_settings(settings: Settings) -> Settings:
    return replace(
        settings,
        rag_url=RAG,
        rag_api_key="rag-key",
        rag_owner_id="00000000-0000-0000-0000-000000000001",
        rag_project_id="4b14572f-c62f-40bd-b6e0-79530f955d73",
        rag_collection="ahawr-code",
        rag_cooldown_seconds=60,
        rag_fresh_window_seconds=0,
    )


@pytest.fixture
def fake(rag_settings: Settings) -> Any:
    rag = FakeRag()
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{RAG}/v1/documents/batch").mock(side_effect=rag.batch)
        router.post(f"{RAG}/v1/retrieval/search").mock(side_effect=rag.search)
        router.delete(url__regex=rf"{RAG}/v1/documents/.*").mock(side_effect=rag.delete)
        yield rag, router


def test_mirror_pushes_chunks_with_provenance(
    rag_settings: Settings, workspace: Path, fake: Any
) -> None:
    rag, _ = fake
    service = make_service(rag_settings)
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        active = [
            c
            for p in (
                "app/parser.py",
                "web/client.ts",
                "docs/guide.md",
                "tests/test_parser.py",
                ".gitignore",
            )
            for c in service.store.chunks_for_path("ws", p).values()
        ]
        assert len(rag.documents) == len(active)
        doc = next(d for d in rag.documents.values() if d["metadata"]["symbol"] == "compute_total")
        assert doc["collection"] == "ahawr-code" and doc["version"] == 1
        assert doc["metadata"]["path"] == "app/parser.py"
        assert doc["external_document_id"] == doc["metadata"]["ahawr_chunk_id"]

        parser = workspace / "app" / "parser.py"
        parser.write_text(parser.read_text().replace("total += float", "total += 2 * float"))
        (workspace / "web" / "client.ts").unlink()
        before = len(rag.documents)
        service.index(IndexRequest(corpus_id="ws"))
        changed = [d for d in rag.documents.values() if d["version"] == 2]
        assert [d["metadata"]["symbol"] for d in changed] == ["compute_total"]
        assert rag.deleted and len(rag.documents) < before
        assert service.store.mirror_pending("ws") == 0
    finally:
        service.close()


def test_rag_backend_serves_and_validates_hits(
    rag_settings: Settings, workspace: Path, fake: Any
) -> None:
    rag, _ = fake
    service = make_service(rag_settings)
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        response = service.retrieve(
            RetrieveRequest(corpora=["ws"], query="retry policy attempts", cache="bypass")
        )
        assert response.stats["backend"] == "rag_platform"
        assert {c["mode"] for c in rag.search_calls} == {"lexical", "dense"}
        assert any(c.symbol == "RetryPolicy" for c in response.chunks)
        assert all(c.freshness == "verified" for c in response.chunks)

        retry = next(c for c in response.chunks if c.symbol == "RetryPolicy")
        rag.stale[retry.chunk_id] = "outdated-hash"
        stale = service.retrieve(
            RetrieveRequest(
                corpora=["ws"],
                query="retry policy attempts",
                cache="bypass",
                options={"retrievers": {"symbol": False}},
            )
        )
        assert all(c.chunk_id != retry.chunk_id for c in stale.chunks)
        assert stale.stats["filtered_by_reason"].get("remote_stale", 0) >= 1
    finally:
        service.close()


def test_rag_outage_falls_back_to_local_and_opens_circuit(
    rag_settings: Settings, workspace: Path, fake: Any
) -> None:
    rag, router = fake
    service = make_service(rag_settings)
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        router.post(f"{RAG}/v1/retrieval/search").mock(side_effect=httpx.ConnectError("down"))
        response = service.retrieve(RetrieveRequest(corpora=["ws"], query="compute_total"))
        assert response.stats["backend"] == "local"
        assert "rag_platform_unavailable:fallback_local" in response.degraded_reasons
        assert response.chunks and response.chunks[0].symbol == "compute_total"
        assert service.rag is not None and not service.rag.available()
        calls = len(router.calls)
        again = service.retrieve(
            RetrieveRequest(corpora=["ws"], query="parse_invoice", cache="bypass")
        )
        assert again.stats["backend"] == "local"
        assert len(router.calls) == calls  # circuit open: no remote call
        assert service.health()["backend"]["rag_platform"]["circuit_open"] is True
    finally:
        service.close()


def test_mirror_failure_keeps_changes_pending(
    rag_settings: Settings, workspace: Path, fake: Any
) -> None:
    _, router = fake
    router.post(f"{RAG}/v1/documents/batch").mock(side_effect=httpx.ConnectError("down"))
    service = make_service(rag_settings)
    try:
        result = service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        assert "rag_mirror_pending" in result.degraded_reasons
        assert result.chunks["added"] > 0  # local index unaffected
        assert service.store.mirror_pending("ws") > 0
    finally:
        service.close()


def test_local_backend_forced_never_calls_rag(
    rag_settings: Settings, workspace: Path, fake: Any
) -> None:
    rag, _ = fake
    service: RetrievalService = make_service(replace(rag_settings, rag_mirror=False))
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        response = service.retrieve(
            RetrieveRequest(corpora=["ws"], query="compute_total", options={"backend": "local"})
        )
        assert response.stats["backend"] == "local" and not rag.search_calls
        assert not rag.documents
    finally:
        service.close()
