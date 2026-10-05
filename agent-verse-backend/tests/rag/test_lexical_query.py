"""Query analysis for the lexical legs (P2-1, P2-3)."""

from __future__ import annotations

from app.rag.lexical_query import (
    MAX_LEXICAL_TERMS,
    analyze_query,
    identifier_parts,
    is_identifier,
)


def test_stop_words_are_dropped_and_identifiers_come_first() -> None:
    q = analyze_query("How much does an out-of-gauge lift cost for job TJ-5531?")
    assert q.terms[:2] == ("out-of-gauge", "TJ-5531")
    assert "how" not in q.terms and "much" not in q.terms and "an" not in q.terms
    assert {"lift", "cost", "job"} <= set(q.terms)
    assert q.identifiers == ("out-of-gauge", "TJ-5531")


def test_long_queries_are_bounded() -> None:
    words = " ".join(f"word{i}x" for i in range(100))
    q = analyze_query(words)
    assert len(q.terms) == MAX_LEXICAL_TERMS
    assert q.is_long


def test_unicode_hyphens_fold_to_ascii() -> None:
    assert analyze_query("job TJ‑5531").identifiers == ("TJ-5531",)
    assert identifier_parts("TJ‑5531") == ["tj", "5531"]


def test_identifier_detection() -> None:
    assert is_identifier("TJ-5531")
    assert is_identifier("H1")
    assert is_identifier("diesel_cost_inr")
    assert not is_identifier("diesel")
    assert not is_identifier("2027")


def test_stop_word_only_query_has_no_terms() -> None:
    assert analyze_query("how is it").terms == ()
