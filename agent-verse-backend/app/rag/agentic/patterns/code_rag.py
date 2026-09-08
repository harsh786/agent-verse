"""Code RAG — identifier/symbol-aware boosting on top of hybrid retrieval.

Code questions ("what does `parse_request_id` do", "where is `TenantContext`
constructed") are dominated by exact-symbol recall: the chunk that actually
defines or calls the identifier the user typed is almost always the right
answer, far more reliably than pure semantic similarity over prose-shaped
embeddings. This module implements the query-time half of that idea:

  1. Extract likely code identifiers from the natural-language query
     (``snake_case``, ``camelCase``, ``PascalCase``, dotted attribute paths,
     and ``function_call(`` syntax).
  2. Re-rank a hybrid-search candidate set, promoting chunks that contain an
     exact, case-sensitive occurrence of one of those identifiers ahead of
     chunks that only scored well on embedding/lexical similarity.

This is deliberately a real, deterministic, LLM-free re-ranking pass (no
provider call is required), so it degrades to plain hybrid-search ordering
whenever the query carries no code-shaped tokens at all.
"""

from __future__ import annotations

import re

from app.rag.engine import RetrievalResult

# snake_case (>=1 underscore), camelCase/PascalCase (an internal case change),
# or a dotted attribute/module path (`a.b`, `pkg.mod.Class`).
_IDENTIFIER_PATTERN = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b"  # dotted.path
    r"|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"  # snake_case
    r"|\b[a-z][a-z0-9]*(?:[A-Z][a-z0-9]*)+\b"  # camelCase
    r"|\b[A-Z][a-z0-9]*(?:[A-Z][a-z0-9]*)+\b"  # PascalCase
)
_FUNCTION_CALL_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_CODE_INTENT_TERMS = frozenset(
    {
        "function",
        "method",
        "class",
        "def ",
        "import",
        "module",
        "variable",
        "parameter",
        "argument",
        "exception",
        "stack trace",
        "traceback",
        "codebase",
        "repository",
        "endpoint",
        "api call",
        "refactor",
        "implementation",
        "source code",
    }
)


def extract_code_symbols(query: str) -> list[str]:
    """Extract likely identifier/symbol tokens from a natural-language query.

    Order-preserving and de-duplicated. Recognizes ``snake_case``,
    ``camelCase``, ``PascalCase``, dotted attribute paths, and the target of a
    ``name(`` function-call expression.
    """
    seen: set[str] = set()
    symbols: list[str] = []
    for match in (
        *_IDENTIFIER_PATTERN.finditer(query),
        *_FUNCTION_CALL_PATTERN.finditer(query),
    ):
        symbol = match.group(1) if match.re is _FUNCTION_CALL_PATTERN else match.group(0)
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def has_code_intent(query: str) -> bool:
    """Return whether a query is plausibly asking about source code."""
    if extract_code_symbols(query):
        return True
    normalized = query.lower()
    return any(term in normalized for term in _CODE_INTENT_TERMS)


def boost_symbol_matches(
    symbols: list[str],
    results: list[RetrievalResult],
    *,
    top_k: int,
) -> list[RetrievalResult]:
    """Re-rank hybrid-search candidates, promoting exact symbol occurrences.

    Chunks containing more of the query's extracted identifiers sort first;
    ties fall back to the original retrieval score, then ``chunk_id`` for
    determinism. Every result gains a ``matched_symbols`` metadata count (0
    when no symbol was extracted or none matched) so the decision is
    observable in the strategy trace.
    """
    if not symbols:
        for result in results:
            result.source_metadata = {**result.source_metadata, "matched_symbols": 0}
        return results[:top_k]

    def symbol_hits(result: RetrievalResult) -> int:
        return sum(1 for symbol in symbols if symbol in result.content)

    hits_by_id = {result.chunk_id: symbol_hits(result) for result in results}
    ranked = sorted(
        results,
        key=lambda result: (-hits_by_id[result.chunk_id], -result.score, result.chunk_id),
    )
    for result in ranked:
        result.source_metadata = {
            **result.source_metadata,
            "matched_symbols": hits_by_id[result.chunk_id],
        }
    return ranked[:top_k]
