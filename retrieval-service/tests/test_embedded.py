import base64
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import respx

from ahawr_retrieval.cli import main as cli_main
from ahawr_retrieval.config import Settings
from ahawr_retrieval.embedded import decode_payload, execute
from ahawr_retrieval.embeddings import OpenAICompatibleEmbedder
from ahawr_retrieval.models import IndexRequest
from ahawr_retrieval.rag_platform import RagPlatformClient, RagPlatformUnavailable
from ahawr_retrieval.reranker import NoopReranker
from ahawr_retrieval.service import RetrievalService
from ahawr_retrieval.store import Store


def payload(workspace: Path, **request: object) -> dict[str, object]:
    return {
        "action": "retrieve",
        "bootstrap": [{"corpus_id": "ws", "root": str(workspace)}],
        "request": {
            "profile": "worker",
            "corpora": ["ws"],
            "task": {"task_id": "T1", "title": "Fix compute_total tax lines"},
            **request,
        },
    }


def test_decode_payload_variants() -> None:
    raw = {"action": "health"}
    token = base64.b64encode(json.dumps(raw).encode()).decode()
    assert decode_payload(token) == raw
    assert decode_payload(f"'{token}'") == raw
    assert decode_payload(json.dumps(raw)) == raw
    with pytest.raises(ValueError):
        decode_payload("not base64 !")


def test_execute_bootstraps_then_serves_from_shared_index(
    settings: Settings, workspace: Path
) -> None:
    first = execute(payload(workspace), settings)
    assert first["ok"] and first["bootstrapped"] == ["ws"]
    chunks = first["response"]["chunks"]
    assert chunks[0]["symbol"] == "compute_total"
    assert first["response"]["context"].startswith("=== RETRIEVED CONTEXT")
    # a new process (new service instance) reuses the on-disk index and cache
    second = execute(payload(workspace), settings)
    assert second["ok"] and second["bootstrapped"] == []
    assert second["response"]["cache"]["status"] == "hit"


def test_execute_reports_errors_as_json(settings: Settings, workspace: Path) -> None:
    missing = execute(
        {"action": "retrieve", "request": {"corpora": ["absent"], "query": "x"}}, settings
    )
    assert missing == {
        "ok": False,
        "action": "retrieve",
        "bootstrapped": [],
        "error": missing["error"],
        "status_code": 404,
    }
    invalid = execute({"action": "retrieve", "request": {"corpora": ["ws"]}}, settings)
    assert invalid["status_code"] == 422 and not invalid["ok"]
    assert execute({"action": "drop-tables"}, settings)["status_code"] == 400
    outside = execute({"action": "index", "request": {"corpus_id": "x", "root": "/"}}, settings)
    assert outside["status_code"] == 400
    indexed = execute(
        {"action": "index", "request": {"corpus_id": "ws", "root": str(workspace)}}, settings
    )
    assert indexed["ok"] and indexed["response"]["code_generation"] == 1
    inv = execute(
        {"action": "invalidate", "request": {"corpus_id": "ws", "paths": ["app/parser.py"]}},
        settings,
    )
    assert inv["ok"] and inv["response"]["invalidated_chunks"] > 0
    assert execute({"action": "health"}, settings)["response"]["status"] == "ok"


def test_cli_exec_always_prints_json(
    settings: Settings,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("RETRIEVAL_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("RETRIEVAL_ALLOWED_ROOTS", ",".join(settings.allowed_roots))
    token = base64.b64encode(json.dumps(payload(workspace)).encode()).decode()
    assert cli_main(["exec", token]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["ok"] and result["response"]["chunks"]
    assert cli_main(["exec", "%%%"]) == 0
    broken = json.loads(capsys.readouterr().out.strip())
    assert broken["ok"] is False and broken["status_code"] == 400


def test_circuit_breaker_state_survives_processes(settings: Settings) -> None:
    configured = replace(
        settings,
        rag_url="http://rag:8100",
        rag_api_key="k",
        rag_owner_id="00000000-0000-0000-0000-000000000001",
        rag_project_id="4b14572f-c62f-40bd-b6e0-79530f955d73",
        rag_collection="c",
        rag_cooldown_seconds=60,
    )
    store = Store(settings.data_dir / "state.sqlite")
    try:
        with respx.mock:
            respx.post("http://rag:8100/v1/retrieval/search").mock(
                side_effect=httpx.ConnectError("down")
            )
            first = RagPlatformClient(configured, state=store)
            with pytest.raises(RagPlatformUnavailable):
                first.search("ws", "q", "q", ["code"], 5, 5)
        second = RagPlatformClient(configured, state=store)  # "next process"
        assert not second.available()
        assert "ConnectError" in (second.status()["last_error"] or "")
    finally:
        store.close()


@respx.mock
def test_interrupted_embedding_is_backfilled_by_next_sync(
    settings: Settings, workspace: Path
) -> None:
    route = respx.post("http://emb:8080/embeddings")
    route.mock(return_value=httpx.Response(503))
    service = RetrievalService(
        settings, embedder=OpenAICompatibleEmbedder("http://emb:8080", "m"), reranker=NoopReranker()
    )
    try:
        first = service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        assert first.embeddings["pending"] > 0
        # while backing off, a sync does not hammer the unavailable embedder
        calls = route.call_count
        service.index(IndexRequest(corpus_id="ws", mode="full"))
        assert route.call_count == calls

        def ok(request: httpx.Request) -> httpx.Response:
            inputs = json.loads(request.content)["input"]
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": i, "embedding": [1.0, float(len(t) % 5), 0.5]}
                        for i, t in enumerate(inputs)
                    ]
                },
            )

        route.mock(side_effect=ok)
        service.store.set_meta(f"embed_backoff_until:{service.embedder.model_id}", "0")
        second = service.index(IndexRequest(corpus_id="ws"))  # no file changed
        assert second.embeddings["computed"] == first.embeddings["pending"]
        assert not service.store.chunks_missing_embeddings(service.embedder.model_id, "ws")
    finally:
        service.close()
