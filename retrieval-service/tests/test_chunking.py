from __future__ import annotations

import pytest

from ahawr_retrieval.chunking import ChunkingConfig, chunk_file

from .conftest import PARSER_PY

# 20 functions with 30-line bodies: default chunking leaves each function whole (31 lines),
# so the 10-line fragment limit must split them into more chunks.
LONG_PY = "".join(
    f"def fn_{i}():\n" + "".join(f"    x_{j} = {j}\n" for j in range(30))
    for i in range(20)
)


def test_default_chunking_unchanged() -> None:
    chunked = chunk_file("app/parser.py", PARSER_PY)
    assert len(chunked.chunks) > 0
    # Default ChunkingConfig uses max_lines=80, max_chars=3200.
    for c in chunked.chunks:
        assert c.start_line >= 1


def test_shorter_fragment_length_limit_applied() -> None:
    # With a very low max_chars, the same file should split into more chunks.
    original = chunk_file("app/parser.py", PARSER_PY)
    shorter = chunk_file("app/parser.py", PARSER_PY, ChunkingConfig(max_chars=200))
    assert len(shorter.chunks) >= len(original.chunks)


def test_env_shorter_fragment_limit_changes_chunk_file(monkeypatch: pytest.MonkeyPatch) -> None:
    default = chunk_file("app/long.py", LONG_PY)
    assert max(c.end_line - c.start_line + 1 for c in default.chunks) > 10
    monkeypatch.setattr("ahawr_retrieval.chunking.RETRIEVAL_MAX_FRAGMENT_LINES", 10)
    monkeypatch.setattr("ahawr_retrieval.chunking.RETRIEVAL_MAX_FRAGMENT_CHARS", 200)
    shorter = chunk_file("app/long.py", LONG_PY)
    assert len(shorter.chunks) > len(default.chunks)
    for c in shorter.chunks:
        assert c.end_line - c.start_line + 1 <= 10


def test_env_max_fragment_chars_adds_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    default = chunk_file("app/long.py", LONG_PY)
    monkeypatch.setattr("ahawr_retrieval.chunking.RETRIEVAL_MAX_FRAGMENT_CHARS", 200)
    shorter = chunk_file("app/long.py", LONG_PY)
    assert len(shorter.chunks) > len(default.chunks)
