from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ahawr_retrieval.indexer import IndexingError
from ahawr_retrieval.models import IndexDocument, IndexRequest, InvalidateRequest
from ahawr_retrieval.service import RetrievalService
from ahawr_retrieval.store import ACTIVE, DELETED, INVALIDATED


def paths(service: RetrievalService, corpus: str = "ws") -> set[str]:
    return {p for p, f in service.store.files(corpus).items() if f.status == ACTIVE}


def test_initial_index_excludes_secrets_ignored_and_vendor(
    service: RetrievalService, workspace: Path
) -> None:
    result = service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
    assert paths(service) == {
        "app/parser.py",
        "tests/test_parser.py",
        "web/client.ts",
        "docs/guide.md",
    }
    assert result.chunks["added"] > 5
    assert result.embeddings["computed"] > 0
    assert result.code_generation == 1 and result.docs_generation == 1
    assert result.code_snapshot and result.code_snapshot.startswith("tree:")
    assert result.docs_snapshot and result.docs_snapshot.startswith("docs:")


def test_build_files_are_indexed_and_agent_plans_and_backups_are_not(
    service: RetrievalService, workspace: Path
) -> None:
    files = {
        "Dockerfile.llama": "FROM ubuntu:24.04\nRUN patch -p1 < /tmp/a.patch\n",
        "patches/a.patch": "diff --git a/x.c b/x.c\n--- a/x.c\n+++ b/x.c\n@@ -1 +1 @@\n-a\n+b\n",
        "task-plan.json": '{"tasks": []}\n',
        ".hermes/plans/task_plan.json": '{"tasks": []}\n',
        ".claude/settings.json": "{}\n",
        "docker-compose_backup13092026.yml": "services: {}\n",
        "app/parser.py.orig": "old = 1\n",
    }
    for rel, text in files.items():
        (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
        (workspace / rel).write_text(text, encoding="utf-8")
    service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
    indexed = paths(service)
    assert {"Dockerfile.llama", "patches/a.patch"} <= indexed
    assert not indexed & {
        "task-plan.json",
        ".hermes/plans/task_plan.json",
        ".claude/settings.json",
        "docker-compose_backup13092026.yml",
        "app/parser.py.orig",
    }


def test_root_outside_allowed_roots_is_rejected(service: RetrievalService) -> None:
    with pytest.raises(IndexingError):
        service.index(IndexRequest(corpus_id="ws", root="/etc"))


def test_unchanged_sync_is_a_noop(indexed: RetrievalService) -> None:
    result = indexed.index(IndexRequest(corpus_id="ws"))
    assert result.files["unchanged"] == 4
    assert sum(result.chunks.values()) == 0
    assert result.code_generation == 1


def test_editing_one_function_changes_only_that_chunk(
    indexed: RetrievalService, workspace: Path
) -> None:
    parser = workspace / "app" / "parser.py"
    before = indexed.store.chunks_for_path("ws", "app/parser.py")
    parser.write_text(
        parser.read_text().replace("total += float", "total += 1.0 * float"), encoding="utf-8"
    )
    result = indexed.index(IndexRequest(corpus_id="ws"))
    assert result.chunks["modified"] == 1
    assert result.chunks["added"] == 0 and result.chunks["deleted"] == 0
    assert result.embeddings["computed"] == 1
    assert result.code_generation == 2
    assert result.docs_generation == 1  # docs untouched by a code edit
    after = indexed.store.chunks_for_path("ws", "app/parser.py")
    changed = [cid for cid in before if before[cid].content_hash != after[cid].content_hash]
    assert len(changed) == 1 and after[changed[0]].symbol == "compute_total"
    unchanged = next(c for c in after.values() if c.symbol == "parse_invoice")
    assert unchanged.file_hash == after[changed[0]].file_hash  # metadata refreshed


def test_doc_edit_bumps_only_docs_generation(indexed: RetrievalService, workspace: Path) -> None:
    guide = workspace / "docs" / "guide.md"
    guide.write_text(guide.read_text() + "\n## Refunds\n\nRefunds are manual.\n")
    result = indexed.index(IndexRequest(corpus_id="ws"))
    assert result.docs_generation == 2 and result.code_generation == 1
    assert result.chunks["added"] == 1


def test_deleted_file_tombstones_its_chunks(indexed: RetrievalService, workspace: Path) -> None:
    (workspace / "web" / "client.ts").unlink()
    result = indexed.index(IndexRequest(corpus_id="ws"))
    assert result.files["deleted"] == 1
    chunks = indexed.store.chunks_for_path("ws", "web/client.ts")
    assert chunks and all(c.status == DELETED for c in chunks.values())
    assert indexed.store.files("ws")["web/client.ts"].status == DELETED


def test_invalidate_and_reindex_revalidates_without_reembedding(
    indexed: RetrievalService,
) -> None:
    response = indexed.invalidate(
        InvalidateRequest(corpus_id="ws", paths=["app/parser.py"], reason="suspect")
    )
    assert response.invalidated_chunks > 0 and response.invalidated_files == 1
    assert response.code_generation == 2 and response.docs_generation == 1
    chunks = indexed.store.chunks_for_path("ws", "app/parser.py")
    assert all(c.status == INVALIDATED for c in chunks.values())
    other = indexed.store.chunks_for_path("ws", "web/client.ts")
    assert all(c.status == ACTIVE for c in other.values())

    response = indexed.invalidate(
        InvalidateRequest(corpus_id="ws", paths=["app/parser.py"], reindex=True)
    )
    assert response.invalidated_chunks == 0
    # the earlier invalidation stays until reindex, which revalidates without new embeddings
    reindex = indexed.index(IndexRequest(corpus_id="ws", paths=["app/parser.py"], mode="full"))
    assert reindex.embeddings["computed"] == 0
    assert reindex.chunks["revalidated"] > 0
    chunks = indexed.store.chunks_for_path("ws", "app/parser.py")
    assert all(c.status == ACTIVE for c in chunks.values())


def test_invalidate_with_reindex_restores_single_chunk(indexed: RetrievalService) -> None:
    target = next(
        c
        for c in indexed.store.chunks_for_path("ws", "app/parser.py").values()
        if c.symbol == "compute_total"
    )
    response = indexed.invalidate(
        InvalidateRequest(corpus_id="ws", chunk_ids=[target.chunk_id], reindex=True)
    )
    assert response.invalidated_chunks == 1
    assert response.reindex is not None
    assert response.reindex.chunks["revalidated"] == 1
    assert response.reindex.embeddings["computed"] == 0
    assert indexed.store.get_chunks([target.chunk_id])[target.chunk_id].status == ACTIVE


def test_inline_documents_versioning_and_replace(service: RetrievalService) -> None:
    docs = [
        IndexDocument(path="runbook/deploy.md", content="# Deploy\n\nUse blue-green.", version="7"),
        IndexDocument(path="runbook/rollback.md", content="# Rollback\n\nRevert image."),
    ]
    first = service.index(IndexRequest(corpus_id="kb", documents=docs))
    assert first.files["added"] == 2
    files = service.store.files("kb")
    assert files["runbook/deploy.md"].version == "7"
    assert files["runbook/deploy.md"].origin == "inline"
    second = service.index(
        IndexRequest(
            corpus_id="kb",
            documents=docs[:1],
            documents_mode="replace",
        )
    )
    assert second.files["unchanged"] == 1 and second.files["deleted"] == 1


def test_inline_document_expiry_is_recorded(service: RetrievalService) -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    service.index(
        IndexRequest(
            corpus_id="kb",
            documents=[
                IndexDocument(
                    path="old.md", content="# Old\n\nObsolete policy text.", valid_until=past
                ),
            ],
        )
    )
    assert service.store.files("kb")["old.md"].valid_until == pytest.approx(past.timestamp())


def test_invalid_relative_path_rejected(service: RetrievalService) -> None:
    with pytest.raises(IndexingError):
        service.index(
            IndexRequest(
                corpus_id="kb",
                documents=[
                    IndexDocument(path="../escape.md", content="x"),
                ],
            )
        )


def test_invalidation_survives_routine_sync_until_content_changes(
    indexed: RetrievalService, workspace: Path
) -> None:
    indexed.invalidate(InvalidateRequest(corpus_id="ws", paths=["app/parser.py"]))
    parser = workspace / "app" / "parser.py"
    parser.touch()  # new mtime, same content
    indexed.index(IndexRequest(corpus_id="ws"))
    chunks = indexed.store.chunks_for_path("ws", "app/parser.py")
    assert all(c.status == INVALIDATED for c in chunks.values())

    parser.write_text(parser.read_text().replace("total += float", "total += 3 * float"))
    result = indexed.index(IndexRequest(corpus_id="ws"))
    assert result.chunks["modified"] == 1
    chunks = indexed.store.chunks_for_path("ws", "app/parser.py")
    by_symbol = {c.symbol: c.status for c in chunks.values()}
    assert by_symbol["compute_total"] == ACTIVE  # changed content is fresh evidence
    assert by_symbol["parse_invoice"] == INVALIDATED  # unchanged content keeps its verdict
