from __future__ import annotations

import pytest

from ahawr_retrieval.candidates import Candidate
from ahawr_retrieval.context import PRECEDENCE_NOTE, RETRIEVAL_RENDER_PATH_HEADER, render_context
from ahawr_retrieval.store import ChunkRecord


def make_record(path: str, content: str = "line 1\nline 2\nline 3") -> ChunkRecord:
    return ChunkRecord(
        id=1,
        chunk_id="ch1",
        corpus_id="ws",
        path=path,
        source_type="code",
        anchor="a",
        symbol="s",
        symbol_kind="function",
        section=None,
        language="python",
        start_line=1,
        end_line=3,
        content=content,
        content_hash="h1",
        file_hash="fh",
        version="v",
        snapshot_id="snap",
        token_count=10,
        status="active",
        status_reason=None,
        generation=1,
        indexed_at=0.0,
        updated_at=0.0,
    )


def _default_output(candidate: Candidate) -> str:
    meta = (
        "--- [1] current_code | path=app/parser.py | lines=1-3 | symbol=s | "
        "chunk=ch1 | sha256=h1 | version=v | snapshot=snap | freshness=indexed | score=1.000"
    )
    return (
        "=== RETRIEVED CONTEXT profile=worker request=req1 chunks=1 ===\n"
        + PRECEDENCE_NOTE
        + "\n"
        + meta
        + "\n"
        "line 1\nline 2\nline 3\n"
        "=== END RETRIEVED CONTEXT ==="
    )


def test_default_context_no_path_header() -> None:
    record = make_record("app/parser.py")
    candidate = Candidate(chunk_id="ch1", record=record, final=1.0)
    assert render_context([candidate], "worker", "req1") == _default_output(candidate)


def test_path_header_flag_off_by_default() -> None:
    assert RETRIEVAL_RENDER_PATH_HEADER is False


def test_path_header_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ahawr_retrieval.context.RETRIEVAL_RENDER_PATH_HEADER", True)
    record = make_record("app/parser.py")
    candidate = Candidate(chunk_id="ch1", record=record, final=1.0)
    default = _default_output(candidate)
    result = render_context([candidate], "worker", "req1")
    assert result == default.replace(
        "line 1\nline 2\nline 3", "path: app/parser.py\nline 1\nline 2\nline 3"
    )
