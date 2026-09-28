from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from ahawr_retrieval.candidates import Candidate
from ahawr_retrieval.config import Settings
from ahawr_retrieval.context import chunk_cost, render_context, select_context
from ahawr_retrieval.models import IndexDocument, IndexRequest, RetrieveRequest
from ahawr_retrieval.profiles import BudgetConfig
from ahawr_retrieval.reranker import HttpReranker
from ahawr_retrieval.service import RetrievalError, RetrievalService
from ahawr_retrieval.store import ChunkRecord

from .conftest import make_service

TASK = {
    "mission_id": "m1",
    "task_id": "T001",
    "title": "Fix invoice totals",
    "objective": "compute_total must not count tax lines twice when summing invoice totals",
    "acceptance_criteria": ["tests/test_parser.py passes"],
    "verification": ["pytest tests/test_parser.py"],
    "scope": ["app/parser.py"],
}


def retrieve(service: RetrievalService, **overrides: Any) -> Any:
    payload: dict[str, Any] = {"profile": "worker", "corpora": ["ws"], "task": TASK}
    payload.update(overrides)
    return service.retrieve(RetrieveRequest(**payload))


def test_worker_retrieval_returns_provenance(indexed: RetrievalService) -> None:
    response = retrieve(indexed)
    assert response.chunks
    top = response.chunks[0]
    assert top.path == "app/parser.py" and top.symbol == "compute_total"
    assert top.rank == 1 and top.source_type == "code" and top.authority == "current_code"
    assert top.content_hash.startswith("sha256:") and top.file_hash.startswith("sha256:")
    assert top.snapshot_id and top.snapshot_id.startswith("tree:")
    assert top.freshness == "verified"
    assert top.scores.lexical is not None and top.scores.fused is not None
    assert top.features["exact_symbol"] == 1.0 and top.features["scope_match"] == 1.0
    assert "=== RETRIEVED CONTEXT profile=worker" in response.context
    assert "path=app/parser.py" in response.context and "symbol=compute_total" in response.context
    assert response.snapshots["ws"]["code_generation"] == 1
    assert "reranker_not_configured" in response.degraded_reasons
    assert response.stats["backend"] == "local"


def test_docs_are_retrieved_with_section_provenance(indexed: RetrievalService) -> None:
    response = retrieve(indexed, query="How are payment retries bounded?", task={})
    doc = next(c for c in response.chunks if c.source_type == "doc")
    assert doc.section == "Billing Guide > Payment retries"
    assert doc.authority == "current_documentation"


def test_reviewer_profile_prefers_tests_and_changed_files(indexed: RetrievalService) -> None:
    response = retrieve(
        indexed,
        profile="reviewer",
        review={
            "worker_output": "FIX APPLIED: updated compute_total in app/parser.py",
            "changed_files": ["app/parser.py"],
        },
    )
    paths = [c.path for c in response.chunks]
    assert "tests/test_parser.py" in paths[:4]
    assert response.profile == "reviewer"


def test_budget_limits_are_enforced(indexed: RetrievalService) -> None:
    response = retrieve(indexed, budget={"max_chunks": 2, "max_tokens": 5000})
    assert len(response.chunks) <= 2
    assert response.context_tokens <= 5000
    per_path: dict[str, int] = {}
    for chunk in retrieve(indexed).chunks:
        per_path[chunk.path] = per_path.get(chunk.path, 0) + 1
    assert max(per_path.values()) <= BudgetConfig().per_path_limit


def _chunk_candidate(n: int, path: str) -> Candidate:
    record = ChunkRecord(
        id=n,
        chunk_id=f"ch{n}",
        corpus_id="ws",
        path=path,
        source_type="code",
        anchor=f"a{n}",
        symbol=f"f{n}",
        symbol_kind="function",
        section=None,
        language="python",
        start_line=n * 10 + 1,
        end_line=n * 10 + 5,
        content=f"def f{n}(): ...",
        content_hash=f"h{n}",
        file_hash="fh",
        version="v",
        snapshot_id=None,
        token_count=50,
        status="active",
        status_reason=None,
        generation=1,
        indexed_at=0.0,
        updated_at=0.0,
    )
    return Candidate(chunk_id=f"ch{n}", record=record, final=1.0)


def test_per_path_limit_keeps_several_chunks_of_one_file() -> None:
    # A task centred on one large module needs more than a few of its chunks.
    ranked = [_chunk_candidate(n, "app/api/main.py") for n in range(10)]
    ranked.append(_chunk_candidate(10, "app/config.py"))
    budget = BudgetConfig()
    selected = select_context(ranked, budget, max_chunks=12, max_tokens=6000)
    main = [c for c in selected if c.record and c.record.path == "app/api/main.py"]
    assert len(main) == budget.per_path_limit == 8
    assert [c.selection_reason for c in ranked[8:10]] == ["per_path_limit"] * 2
    assert ranked[10].selected


def test_weak_chunks_are_left_out_of_the_context() -> None:
    strong, weak = _chunk_candidate(1, "app/a.py"), _chunk_candidate(2, "app/b.py")
    weak.final = 0.45
    selected = select_context([strong, weak], BudgetConfig(), max_chunks=12, max_tokens=6000)
    assert selected == [strong]
    assert weak.selection_reason == "below_min_score"
    kept = select_context(
        [strong, weak], BudgetConfig(min_final_score=0.0), max_chunks=12, max_tokens=6000
    )
    assert kept == [strong, weak]


def test_context_numbers_code_lines_with_their_file_line_numbers(
    indexed: RetrievalService, workspace: Path
) -> None:
    response = retrieve(indexed)
    source = (workspace / "app/parser.py").read_text(encoding="utf-8").splitlines()
    chunk = next(c for c in response.chunks if c.symbol == "compute_total")
    first = chunk.start_line
    assert source[first - 1].startswith("def compute_total")
    width = len(str(chunk.end_line))
    assert f"{first:>{width}}| def compute_total" in response.context
    assert response.context_tokens > sum(c.token_count for c in response.chunks)

    plain = retrieve(indexed, options={"render": {"line_numbers": False}})
    assert "| def compute_total" not in plain.context
    assert "\ndef compute_total" in plain.context
    assert plain.context_tokens == sum(c.token_count for c in plain.chunks)
    assert plain.config_id != response.config_id


def test_line_numbers_are_skipped_when_content_does_not_map_to_lines() -> None:
    exact = _chunk_candidate(1, "app/a.py")  # lines 11-15, one content line
    assert exact.record is not None
    exact.record = replace(exact.record, content="a\nb\nc\nd\ne")
    trimmed = _chunk_candidate(2, "docs/b.md")  # a doc chunk whose blank edges were trimmed
    assert trimmed.record is not None
    trimmed.record = replace(trimmed.record, content="heading\ntext")
    context = render_context([exact, trimmed], "worker", "rr_1", line_numbers=True)
    assert "11| a\n12| b\n13| c\n14| d\n15| e" in context
    assert "\nheading\ntext\n" in context
    assert chunk_cost(exact.record, True) > chunk_cost(exact.record, False) == 50
    assert chunk_cost(trimmed.record, True) == 50


def test_options_switch_retrievers_and_change_config_id(indexed: RetrievalService) -> None:
    lexical = retrieve(indexed, options={"retrievers": {"vector": False, "symbol": False}})
    hybrid = retrieve(indexed)
    assert set(lexical.stats["retrievers"]) == {"lexical"}
    assert lexical.config_id != hybrid.config_id


def test_changed_file_is_filtered_in_verify_mode(
    indexed: RetrievalService, workspace: Path
) -> None:
    parser = workspace / "app" / "parser.py"
    parser.write_text(parser.read_text() + "\n# edited after indexing\n")
    response = retrieve(indexed, freshness_mode="verify", cache="bypass", include_candidates=True)
    assert all(c.path != "app/parser.py" for c in response.chunks)
    assert response.stats["filtered_by_reason"].get("file_changed", 0) > 0
    assert any(c["filtered_reason"] == "file_changed" for c in response.candidates)


def test_sync_mode_reindexes_changed_file_before_retrieval(
    indexed: RetrievalService, workspace: Path
) -> None:
    parser = workspace / "app" / "parser.py"
    parser.write_text(parser.read_text() + "\n\ndef apply_discount(total):\n    return total\n")
    response = retrieve(
        indexed, freshness_mode="sync", query="apply_discount for invoice totals", task={}
    )
    assert any(c.symbol == "apply_discount" for c in response.chunks)
    assert response.snapshots["ws"]["code_generation"] == 2


def test_deleted_file_never_reaches_context(indexed: RetrievalService, workspace: Path) -> None:
    (workspace / "web" / "client.ts").unlink()
    response = retrieve(
        indexed,
        query="RetryPolicy shouldRetry attempts",
        task={},
        freshness_mode="verify",
        cache="bypass",
    )
    assert all(c.path != "web/client.ts" for c in response.chunks)


def test_expired_and_pinned_docs_are_filtered(service: RetrievalService) -> None:
    service.index(
        IndexRequest(
            corpus_id="kb",
            documents=[
                IndexDocument(
                    path="policy.md",
                    content="# Policy\n\nRefund window is 30 days.",
                    version="2",
                    valid_until="2000-01-01T00:00:00Z",
                ),
                IndexDocument(
                    path="faq.md", content="# FAQ\n\nRefund requests go to billing.", version="5"
                ),
            ],
        )
    )
    response = service.retrieve(RetrieveRequest(corpora=["kb"], query="refund window days"))
    assert [c.path for c in response.chunks] == ["faq.md"]
    pinned = service.retrieve(
        RetrieveRequest(
            corpora=["kb"],
            query="refund window days",
            cache="bypass",
            workspace_state={"doc_versions": {"faq.md": "4"}},
        )
    )
    assert pinned.chunks == []


def test_unknown_corpus_and_profile_are_client_errors(indexed: RetrievalService) -> None:
    with pytest.raises(RetrievalError) as missing:
        indexed.retrieve(RetrieveRequest(corpora=["nope"], query="x"))
    assert missing.value.status_code == 404
    with pytest.raises(RetrievalError):
        indexed.retrieve(RetrieveRequest(corpora=["ws"], profile="architect", query="x"))


# ------------------------------------------------------------------------- cache


def test_exact_cache_hit(indexed: RetrievalService) -> None:
    first = retrieve(indexed)
    second = retrieve(indexed)
    assert first.cache.status == "miss" and second.cache.status == "hit"
    assert second.cache.source_request_id == first.request_id
    assert [c.chunk_id for c in second.chunks] == [c.chunk_id for c in first.chunks]
    assert second.chunks[0].freshness == "verified"


def test_reformatted_reviewer_feedback_reuses_retrieval(indexed: RetrievalService) -> None:
    first = retrieve(indexed, review={"feedback": "compute_total still counts tax lines twice."})
    second = retrieve(
        indexed, review={"feedback": "  COMPUTE_TOTAL still counts tax lines twice!! "}
    )
    assert first.cache.status == "miss"
    assert second.cache.status in {"semantic_hit", "hit"}
    assert second.cache.status == "semantic_hit"


def test_new_anchor_in_feedback_triggers_new_retrieval(indexed: RetrievalService) -> None:
    retrieve(indexed, review={"feedback": "compute_total counts tax lines twice"})
    second = retrieve(
        indexed,
        review={"feedback": "compute_total counts tax lines twice; also update docs/guide.md"},
    )
    assert second.cache.status == "miss"
    assert second.cache.reason and second.cache.reason.startswith("query_changed")


def test_cache_is_scoped_per_task(indexed: RetrievalService) -> None:
    retrieve(indexed)
    other = retrieve(indexed, task={**TASK, "task_id": "T002"})
    assert other.cache.status == "miss"


def test_unrelated_change_revalidates_cached_result(
    indexed: RetrievalService, workspace: Path
) -> None:
    first = retrieve(indexed, budget={"max_chunks": 3})
    assert all(c.path != "web/client.ts" for c in first.chunks)
    (workspace / "web" / "client.ts").write_text(
        (workspace / "web" / "client.ts").read_text() + "\nexport const VERSION = 3;\n"
    )
    second = retrieve(indexed, budget={"max_chunks": 3})
    assert second.snapshots["ws"]["code_generation"] == 2
    assert second.cache.status == "revalidated_hit"
    assert [c.chunk_id for c in second.chunks] == [c.chunk_id for c in first.chunks]


def test_relevant_change_invalidates_cached_result(
    indexed: RetrievalService, workspace: Path
) -> None:
    retrieve(indexed, budget={"max_chunks": 3})
    (workspace / "app" / "tax.py").write_text(
        "def skip_tax_lines(lines):\n    return [l for l in lines if 'TAX' not in l]\n"
        "# tax lines must not be counted twice in invoice totals\n"
    )
    second = retrieve(indexed, budget={"max_chunks": 3})
    assert second.cache.status == "miss"
    assert second.cache.reason in {"delta_lexical_relevant", "delta_vector_relevant"}


def test_cache_bypass_and_refresh(indexed: RetrievalService) -> None:
    retrieve(indexed)
    assert retrieve(indexed, cache="bypass").cache.status == "bypass"
    assert retrieve(indexed, cache="refresh").cache.status == "refresh"


# ---------------------------------------------------------------------- reranker


@respx.mock
def test_http_reranker_scores_drive_final_order(settings: Settings, workspace: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        import json

        payload = json.loads(body)
        assert set(payload) >= {"query", "documents", "top_n", "return_documents"}
        assert all(set(doc) == {"id", "text"} for doc in payload["documents"])
        results = []
        for rank, doc in enumerate(payload["documents"], start=1):
            score = 5.0 if "class RetryPolicy" in doc["text"] else -2.0
            results.append({"id": doc["id"], "score": score, "rank": rank})
        return httpx.Response(200, json={"results": results})

    respx.post("http://reranker:8200/v1/rerank").mock(side_effect=handler)
    service = make_service(settings, HttpReranker("http://reranker:8200", api_key="k"))
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        response = service.retrieve(
            RetrieveRequest(corpora=["ws"], query="retry policy attempts for payments")
        )
        top = response.chunks[0]
        assert top.symbol == "RetryPolicy"
        assert top.scores.reranker is not None and top.scores.reranker > 0.99
        assert top.scores.reranker_rank == 1
        assert not response.degraded
    finally:
        service.close()


@respx.mock
def test_reranker_failure_degrades_to_fused_order(settings: Settings, workspace: Path) -> None:
    respx.post("http://reranker:8200/v1/rerank").mock(return_value=httpx.Response(503))
    service = make_service(settings, HttpReranker("http://reranker:8200"))
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        response = service.retrieve(RetrieveRequest(corpora=["ws"], query="compute_total"))
        assert response.chunks
        assert any(r.startswith("reranker_unavailable") for r in response.degraded_reasons)
        assert response.chunks[0].scores.reranker is None
    finally:
        service.close()


# ----------------------------------------------------------------------- logging


def test_every_candidate_is_logged_with_features(indexed: RetrievalService) -> None:
    response = retrieve(
        indexed, trace={"mission_id": "m1", "task_id": "T001", "attempt": 1, "role": "worker"}
    )
    rows = list(indexed.log.iter_feature_rows())
    assert rows and {r["request_id"] for r in rows} == {response.request_id}
    selected = [r for r in rows if r["selected"]]
    assert len(selected) == len(response.chunks)
    row = selected[0]
    for key in (
        "lexical_score",
        "vector_score",
        "fused_score",
        "exact_symbol",
        "path_mentioned",
        "source_type",
        "final_rank",
        "freshness",
        "deterministic_score",
    ):
        assert key in row
    assert row["trace"]["task_id"] == "T001"
    requests = indexed.log.requests()
    assert requests[0]["cache_status"] == "miss" and requests[0]["n_selected"] == len(
        response.chunks
    )
