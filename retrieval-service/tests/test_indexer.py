import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ahawr_retrieval.config import Settings
from ahawr_retrieval.indexer import IndexingError, git_files
from ahawr_retrieval.models import IndexDocument, IndexRequest, InvalidateRequest
from ahawr_retrieval.reranker import NoopReranker
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
        ".gitignore",
    }
    assert result.chunks["added"] > 5
    assert result.embeddings["computed"] > 0
    assert result.code_generation == 1 and result.docs_generation == 1
    assert result.code_snapshot and result.code_snapshot.startswith("tree:")
    assert result.docs_snapshot and result.docs_snapshot.startswith("docs:")


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            (root / rel).write_bytes(content)
        else:
            (root / rel).write_text(content, encoding="utf-8")


BUILD_AND_JUNK: dict[str, str | bytes] = {
    "Dockerfile.llama": "FROM ubuntu:24.04\nRUN patch -p1 < /tmp/a.patch\n",
    "patches/a.patch": "diff --git a/x.c b/x.c\n--- a/x.c\n+++ b/x.c\n@@ -1 +1 @@\n-a\n+b\n",
    "LLAMA_CPP_BASE_COMMIT": "c060ca974c773c7c3d17fd1b66dc9d312bc292c0\n",
    ".hermes/plans/task_plan.json": '{"tasks": []}\n',
    "docker-compose_backup13092026.yml": "services: {}\n",
    "model.gguf": b"GGUF\x00\x00binary",
    "blob.dat": b"\x00\x01\x02binary",
}


def test_every_text_file_is_indexed_except_what_gitignore_excludes(
    service: RetrievalService, workspace: Path
) -> None:
    _write(workspace, BUILD_AND_JUNK)
    (workspace / ".gitignore").write_text(
        "generated/\n*.tmp\n.hermes/\n*_backup*\n", encoding="utf-8"
    )
    service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
    indexed = paths(service)
    assert {"Dockerfile.llama", "patches/a.patch", "LLAMA_CPP_BASE_COMMIT"} <= indexed
    assert not indexed & {
        ".hermes/plans/task_plan.json",  # .gitignore
        "docker-compose_backup13092026.yml",  # .gitignore
        "model.gguf",  # binary by name
        "blob.dat",  # binary by content
        ".env",  # secrets are never indexed, ignored or not
        "server.pem",
        "node_modules/lib/index.js",  # vendor directories are never indexed
    }


def test_file_line_count_is_max_end_line_over_active_chunks(
    service: RetrievalService, workspace: Path
) -> None:
    _write(workspace, {"app/long.py": "\n".join(f"line {i}" for i in range(1200))})
    service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
    assert service.store.file_line_count("ws", "app/long.py") == 1200
    # An unindexed path has no active chunks and reports None.
    assert service.store.file_line_count("ws", "app/missing.py") is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_work_tree_uses_git_ignore_rules(service: RetrievalService, workspace: Path) -> None:
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    _write(
        workspace,
        {
            "docs/.gitignore": "draft-*.md\n!draft-keep.md\n",  # nested file with a negation
            "docs/draft-old.md": "# Old draft\n\nstale text\n",
            "docs/draft-keep.md": "# Kept draft\n\ncurrent text\n",
        },
    )
    listed = git_files(workspace)
    assert listed is not None
    assert "docs/draft-keep.md" in listed and "docs/draft-old.md" not in listed
    assert "generated/out.py" not in listed  # root .gitignore
    service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
    indexed = paths(service)
    assert "docs/draft-keep.md" in indexed and "docs/draft-old.md" not in indexed
    assert "app/parser.py" in indexed and ".env" not in indexed
    assert not any(p.startswith("node_modules/") for p in indexed)


def test_git_files_is_none_outside_a_work_tree(workspace: Path) -> None:
    assert git_files(workspace) is None


def test_root_outside_allowed_roots_is_rejected(service: RetrievalService) -> None:
    with pytest.raises(IndexingError):
        service.index(IndexRequest(corpus_id="ws", root="/etc"))


def test_unchanged_sync_is_a_noop(indexed: RetrievalService) -> None:
    result = indexed.index(IndexRequest(corpus_id="ws"))
    assert result.files["unchanged"] == 5  # the four sources and .gitignore
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


def test_backup_and_artifact_files_are_excluded_by_default(
    settings: Settings, workspace: Path
) -> None:
    service = RetrievalService(settings, reranker=NoopReranker())
    try:
        _write(
            workspace,
            {
                "docker-entrypoint_backup2.sh": "#!/bin/sh\nexit 0\n",
                "Dockerfile_backup": "FROM scratch\n",
                "x.bak-20260101": "backup\n",
                "a.orig": "original\n",
                "b.rej": "rejection\n",
                "c~": "emacs backup\n",
                "d.old": "old\n",
            },
        )
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        indexed = paths(service)
        for rel in (
            "docker-entrypoint_backup2.sh",
            "Dockerfile_backup",
            "x.bak-20260101",
            "a.orig",
            "b.rej",
            "c~",
            "d.old",
        ):
            assert rel not in indexed  # backup/edit artefacts are never indexed
        assert "app/parser.py" in indexed  # normal files are still indexed
    finally:
        service.close()


def test_retrieval_exclude_globs_replaces_the_default(settings: Settings, workspace: Path) -> None:
    # The env parsing: RETRIEVAL_EXCLUDE_GLOBS=*.bak,*.orig -> Settings.exclude_globs.
    assert Settings.from_env({"RETRIEVAL_EXCLUDE_GLOBS": "*.bak,*.orig"}).exclude_globs == [
        "*.bak",
        "*.orig",
    ]
    service = RetrievalService(settings, reranker=NoopReranker())
    try:
        _write(workspace, {"a.orig": "original\n", "b.txt": "backup\n"})
        # A custom list ["*backup*", "*.txt"] replaces the default list, so a.orig
        # (matched by the default *.orig) is kept, while b.txt (matched by *.txt)
        # is dropped.
        service.index(
            IndexRequest(
                corpus_id="ws",
                root=str(workspace),
                exclude_globs=["*backup*", "*.txt"],
            )
        )
        indexed = paths(service)
        # custom list replaces the default, so *.orig is no longer excluded
        assert "a.orig" in indexed
        assert "b.txt" not in indexed  # *.txt is in the custom list
        assert "app/parser.py" in indexed  # normal files are still indexed
    finally:
        service.close()


def test_excluded_files_indexed_before_are_removed_on_the_next_sync(
    settings: Settings, workspace: Path
) -> None:
    service = RetrievalService(settings, reranker=NoopReranker())
    try:
        _write(workspace, {"a.orig": "original\n"})
        # a.orig is indexed because ["*backup*"] does not match it (replaces default *.orig).
        service.index(
            IndexRequest(
                corpus_id="ws",
                root=str(workspace),
                exclude_globs=["*backup*"],
            )
        )
        assert "a.orig" in paths(service)

        # Next sync with a glob that now matches a.orig: it disappears from the index.
        result = service.index(
            IndexRequest(
                corpus_id="ws",
                exclude_globs=["*.orig"],
            )
        )
        assert "a.orig" not in paths(service)
        assert result.files["deleted"] >= 1
        file = service.store.files("ws")["a.orig"]
        assert file.status == DELETED
    finally:
        service.close()


def test_stored_corpus_exclude_globs_are_kept_when_the_request_has_none(
    settings: Settings, workspace: Path
) -> None:
    service = RetrievalService(settings, reranker=NoopReranker())
    try:
        service.index(
            IndexRequest(
                corpus_id="ws",
                root=str(workspace),
                exclude_globs=["*.txt"],
            )
        )
        _write(workspace, {"b.txt": "backup\n"})
        # A sync without exclude_globs in the request keeps the stored ["*.txt"].
        result = service.indexer.sync("ws")
        assert result is not None
        assert "b.txt" not in paths(service)
        corpus = service.store.get_corpus("ws")
        assert corpus is not None and corpus.config.get("exclude_globs") == ["*.txt"]
    finally:
        service.close()
