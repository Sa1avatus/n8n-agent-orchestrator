"""Tests for lexical query shaping flags in pg_store."""

from __future__ import annotations

import ahawr_retrieval.pg_store as pg_store
from ahawr_retrieval.pg_store import select_rarest_terms, tsquery_from_match


def test_unchanged_with_flags_off() -> None:
    """With all flags default off, the query must match the original behavior."""
    assert tsquery_from_match('"parse_invoice" OR "test"') == "parsezqzinvoice | test"
    assert tsquery_from_match('"hello world"') == "hello | world"
    assert tsquery_from_match('"invoice.py"') == "invoice | py"
    assert tsquery_from_match("") == ""


def test_boost_path_flag_on(monkeypatch) -> None:
    monkeypatch.setattr(pg_store, "_LEXICAL_BOOST_PATH", True)
    result = tsquery_from_match('"invoice.py"')
    assert result == "invoice <-> py"
    # A path term is emitted as adjacent pieces, not as scattered words
    assert tsquery_from_match('"a/b.c"') == "a <-> b <-> c"
    # Non-path terms are unaffected
    assert tsquery_from_match('"parse_invoice" OR "test"') == "parsezqzinvoice | test"


def test_keep_phrase_flag_on(monkeypatch) -> None:
    monkeypatch.setattr(pg_store, "_LEXICAL_KEEP_PHRASE", True)
    result = tsquery_from_match('"hello world"')
    assert "hello <-> world" in result
    assert result == "hello <-> world"


def test_drop_noise_flag_on(monkeypatch) -> None:
    monkeypatch.setattr(pg_store, "_LEXICAL_DROP_NOISE", True)
    result = tsquery_from_match('"T010" OR "task" OR "work" OR "parse"')
    # Noise terms (task IDs like T010 and generic words) are dropped
    assert "T010" not in result
    assert "task" not in result
    assert "work" not in result
    assert "and" not in result  # no "and" in this query
    assert result == "parse"


def test_shaped_terms_survive_rarest_selection(monkeypatch) -> None:
    """With boost-path on, a shaped path term (a phrase) must survive select_rarest_terms."""

    class FakeStore:
        def _document_frequency(self, terms):
            # A fake frequency: the path phrase is rare, the scattered words are common
            return {
                "invoice <-> py": 1,
                "invoice": 90000,
                "py": 80000,
            }

    store = FakeStore()
    terms = ["invoice <-> py", "invoice", "py"]
    freq = store._document_frequency(terms)
    chosen = select_rarest_terms(terms, freq, limit=3)
    # The rarest term (the path phrase) is kept and ranked first
    assert chosen[0] == "invoice <-> py"
