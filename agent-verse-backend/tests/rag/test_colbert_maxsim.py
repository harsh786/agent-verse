"""ColBERT late-interaction is real token-level MaxSim, not a pgvector approximation.

Proves the scoring math in app.rag.agentic.patterns.colbert.maxsim_score: for each
QUERY token, take the max cosine similarity over all DOCUMENT tokens, then sum —
the defining ColBERT late-interaction (MaxSim) operator. The runtime adapter loads
the real colbert-ir/colbertv2.0 checkpoint via RAGatouille; here we lock in the
operator itself (deterministic, dependency-free).
"""
from __future__ import annotations

import math

from app.rag.agentic.patterns.colbert import maxsim_score


def test_maxsim_perfect_token_matches() -> None:
    # Two query tokens, each has an identical (cosine=1.0) token in the document.
    query = [[1.0, 0.0], [0.0, 1.0]]
    document = [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]
    # Each query token's best match is 1.0 → sum = 2.0.
    assert math.isclose(maxsim_score(query, document), 2.0, rel_tol=1e-9)


def test_maxsim_takes_the_max_over_document_tokens() -> None:
    query = [[1.0, 0.0]]
    # Best cosine for the query token is with the first doc token (1.0), not the
    # orthogonal one (0.0). MaxSim must pick the max, not the average.
    document = [[1.0, 0.0], [0.0, 1.0]]
    assert math.isclose(maxsim_score(query, document), 1.0, rel_tol=1e-9)
    # If it were an average it would be 0.5 — assert it is NOT.
    assert maxsim_score(query, document) != 0.5


def test_maxsim_orthogonal_scores_zero() -> None:
    query = [[1.0, 0.0]]
    document = [[0.0, 1.0]]
    assert math.isclose(maxsim_score(query, document), 0.0, abs_tol=1e-9)


def test_maxsim_sums_across_query_tokens() -> None:
    # Query token A matches at 1.0, query token B matches at ~0.6 → sum ~1.6.
    query = [[1.0, 0.0], [0.6, 0.8]]
    document = [[1.0, 0.0]]
    # A·doc = 1.0 ; B·doc = 0.6 (both unit vectors) → 1.6
    assert math.isclose(maxsim_score(query, document), 1.6, rel_tol=1e-9)


def test_maxsim_empty_inputs_are_zero() -> None:
    assert maxsim_score([], [[1.0, 0.0]]) == 0.0
    assert maxsim_score([[1.0, 0.0]], []) == 0.0


def test_maxsim_is_not_symmetric_query_vs_doc() -> None:
    # Late interaction is asymmetric: sum is over QUERY tokens. A query with more
    # tokens than the doc yields a larger sum than the transpose.
    a = [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]  # 3 query tokens
    b = [[1.0, 0.0]]  # 1 doc token
    assert maxsim_score(a, b) > maxsim_score(b, a)
