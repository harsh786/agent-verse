"""Unit coverage for the code-RAG identifier extraction and boosting (D-9)."""

from __future__ import annotations

from app.rag.agentic.patterns.code_rag import (
    boost_symbol_matches,
    extract_code_symbols,
    has_code_intent,
)
from app.rag.engine import RetrievalResult


def test_extract_code_symbols_finds_snake_case() -> None:
    assert extract_code_symbols("what does parse_request_id do") == ["parse_request_id"]


def test_extract_code_symbols_finds_camel_and_pascal_case() -> None:
    symbols = extract_code_symbols("explain camelCaseName and PascalCaseName usage")
    assert symbols == ["camelCaseName", "PascalCaseName"]


def test_extract_code_symbols_finds_dotted_paths() -> None:
    assert extract_code_symbols("where is app.rag.gateway.execute_core_strategy defined") == [
        "app.rag.gateway.execute_core_strategy"
    ]


def test_extract_code_symbols_finds_function_call_target() -> None:
    assert extract_code_symbols("what happens when normalize(x) runs") == ["normalize"]


def test_extract_code_symbols_deduplicates_preserving_order() -> None:
    symbols = extract_code_symbols("parse_request_id calls parse_request_id again")
    assert symbols == ["parse_request_id"]


def test_extract_code_symbols_empty_for_plain_prose() -> None:
    assert extract_code_symbols("what is our quarterly revenue") == []


def test_has_code_intent_true_for_identifier() -> None:
    assert has_code_intent("what does parse_request_id() do") is True


def test_has_code_intent_true_for_keyword_without_identifier() -> None:
    assert has_code_intent("walk me through the exception handling in this module") is True


def test_has_code_intent_false_for_plain_prose() -> None:
    assert has_code_intent("tell me about our quarterly revenue") is False


def test_boost_symbol_matches_promotes_exact_hits_over_higher_base_score() -> None:
    low_score_match = RetrievalResult("def-1", "def parse_request_id(x): return x", 0.4, {})
    high_score_no_match = RetrievalResult("prose-1", "unrelated prose content", 0.95, {})

    ranked = boost_symbol_matches(
        ["parse_request_id"],
        [high_score_no_match, low_score_match],
        top_k=2,
    )

    assert [result.chunk_id for result in ranked] == ["def-1", "prose-1"]
    assert ranked[0].source_metadata["matched_symbols"] == 1
    assert ranked[1].source_metadata["matched_symbols"] == 0


def test_boost_symbol_matches_falls_back_to_score_when_no_symbols() -> None:
    first = RetrievalResult("a", "first", 0.9, {})
    second = RetrievalResult("b", "second", 0.5, {})

    ranked = boost_symbol_matches([], [second, first], top_k=2)

    assert [result.chunk_id for result in ranked] == ["b", "a"]
    assert all(result.source_metadata["matched_symbols"] == 0 for result in ranked)


def test_boost_symbol_matches_respects_top_k() -> None:
    results = [
        RetrievalResult("a", "def parse_request_id(): pass", 0.1, {}),
        RetrievalResult("b", "def parse_request_id(): pass", 0.2, {}),
        RetrievalResult("c", "no symbol here", 0.9, {}),
    ]

    ranked = boost_symbol_matches(["parse_request_id"], results, top_k=1)

    assert len(ranked) == 1
    assert ranked[0].chunk_id == "b"  # tie-break: higher base score among symbol matches
