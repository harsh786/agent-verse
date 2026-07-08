"""Real cross-encoder reranking using sentence-transformers.

Uses cross-encoder/ms-marco-MiniLM-L-6-v2 — a 22MB model trained on MS MARCO
passage ranking. Scores (query, document) pairs with a proper attention mechanism.

Falls back to TF-IDF scoring when sentence-transformers is not available or
when the model cannot be loaded (e.g., network restrictions).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


_CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_model_instance: Any = None  # module-level singleton


def _get_cross_encoder() -> Any | None:
    """Load cross-encoder model lazily (singleton)."""
    global _model_instance
    if _model_instance is not None:
        return _model_instance
    try:
        from sentence_transformers import CrossEncoder

        _model_instance = CrossEncoder(_CROSS_ENCODER_MODEL, max_length=512)
        return _model_instance
    except Exception:
        return None


def cross_encode(
    query: str,
    documents: list[str],
    batch_size: int = 32,
) -> list[float]:
    """Score (query, doc) pairs with a real cross-encoder.

    Returns scores aligned with input documents order.
    Falls back to TF-IDF if model unavailable.
    """
    model = _get_cross_encoder()
    if model is not None and documents:
        try:
            pairs = [(query, doc[:512]) for doc in documents]
            scores = model.predict(pairs, batch_size=batch_size)
            return [float(s) for s in scores]
        except Exception:
            pass
    # TF-IDF fallback
    return _tfidf_scores(query, documents)


def _tfidf_scores(query: str, documents: list[str]) -> list[float]:
    """TF-IDF weighted token overlap as fallback."""
    q_tokens = set(re.findall(r'\b\w+\b', query.lower()))
    all_doc_tokens = [re.findall(r'\b\w+\b', d.lower()) for d in documents]
    N = len(documents)

    def idf(token: str) -> float:
        df = sum(1 for tokens in all_doc_tokens if token in tokens)
        return math.log((N + 1) / (df + 1)) + 1 if df > 0 else 1.0

    scores = []
    for doc_tokens in all_doc_tokens:
        freq = Counter(doc_tokens)
        total = len(doc_tokens) or 1
        score = sum((freq.get(t, 0) / total) * idf(t) for t in q_tokens)
        scores.append(score)
    return scores


def is_cross_encoder_available() -> bool:
    """Check if sentence-transformers CrossEncoder can be loaded."""
    return _get_cross_encoder() is not None
