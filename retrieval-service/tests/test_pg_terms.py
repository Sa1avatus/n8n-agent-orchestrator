"""The lexical query of the PostgreSQL store keeps only the rarest terms (pg_store.select_rarest_terms)."""

from __future__ import annotations

from ahawr_retrieval.pg_store import LEXICAL_MAX_TERMS, select_rarest_terms


def test_rarest_terms_come_first_and_absent_terms_are_dropped() -> None:
    terms = ["common", "rare", "missing", "middle", "rarest"]
    frequency = {"common": 50000, "rare": 12, "missing": 0, "middle": 900, "rarest": 2}
    assert select_rarest_terms(terms, frequency, 3) == ["rarest", "rare", "middle"]
    assert select_rarest_terms(terms, frequency, 10, row_budget=10**9) == [
        "rarest",
        "rare",
        "middle",
        "common",
    ]


def test_ties_keep_the_original_order_and_unknown_terms_count_as_absent() -> None:
    terms = ["beta", "alpha", "gamma"]
    assert select_rarest_terms(terms, {"beta": 5, "alpha": 5}, 2) == ["beta", "alpha"]
    assert select_rarest_terms(terms, {}, 2) == []


def test_the_cap_is_small_enough_to_score_quickly() -> None:
    assert 8 <= LEXICAL_MAX_TERMS <= 20


def test_the_row_budget_drops_common_terms_but_keeps_a_minimum() -> None:
    terms = ["a", "b", "c", "d", "e"]
    frequency = {"a": 100, "b": 200, "c": 9000, "d": 50000, "e": 70000}
    # a + b + c = 9300 fits the budget, d would exceed it
    assert select_rarest_terms(terms, frequency, 16, row_budget=10000, min_terms=2) == [
        "a",
        "b",
        "c",
    ]
    # when even the rarest terms are common the minimum is kept so the query still matches
    common = {"a": 60000, "b": 70000, "c": 80000, "d": 90000}
    assert select_rarest_terms(["a", "b", "c", "d"], common, 16, row_budget=1000, min_terms=3) == [
        "a",
        "b",
        "c",
    ]
