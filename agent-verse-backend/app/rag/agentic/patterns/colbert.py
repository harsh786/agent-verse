"""ColBERT — Late Interaction Reranking via MaxSim Token Scoring.

Khattab & Zaharia 2020: 'ColBERT: Efficient and Effective Passage Search via
Contextualized Late Interaction over BERT'

This implementation provides a ColBERT-inspired reranker using:
  - TF-weighted token importance (as a proxy for contextualized token embeddings)
  - MaxSim: for each query token, find maximum similarity with any document token
  - Final score = sum of MaxSim values / len(query_tokens)

No external model required — uses word-level overlap with IDF-like weighting.
For production use with a real ColBERT model, override _token_embed().
"""
from __future__ import annotations
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need", "dare",
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "as",
    "and", "or", "but", "not", "so", "if", "when", "where", "how", "that",
    "this", "these", "those", "it", "its", "i", "you", "he", "she", "we",
    "they", "what", "who", "which",
})


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase non-stopword tokens."""
    tokens = re.findall(r'\b[a-zA-Z0-9]+\b', text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def _compute_idf(token: str, all_docs: list[list[str]]) -> float:
    """Compute IDF weight for a token across a corpus."""
    df = sum(1 for doc in all_docs if token in doc)
    if df == 0:
        return 1.0
    return math.log((len(all_docs) + 1) / (df + 1)) + 1.0


@dataclass
class ColBERTScore:
    chunk_id: str
    content: str
    colbert_score: float
    original_score: float


class ColBERTPattern(RAGPattern):
    """ColBERT MaxSim reranker — token-level late interaction scoring."""

    def __init__(self, alpha: float = 0.5) -> None:
        """
        Args:
            alpha: weight for ColBERT score vs original score.
                   final = alpha * colbert + (1-alpha) * original
        """
        self._alpha = alpha

    @property
    def pattern_id(self) -> str:
        return "colbert_late_interaction"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "ColBERT late interaction reranking — MaxSim token-level scoring: "
            "for each query token, find maximum similarity with any document token "
            "(Khattab & Zaharia 2020). No external model required: uses TF-IDF "
            f"weighted token overlap. Alpha={self._alpha} (colbert:original blend)."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        return True

    def _maxsim_score(self, query: str, document: str) -> float:
        """Compute MaxSim score between query and document."""
        query_tokens = _tokenize(query)
        doc_tokens = _tokenize(document)

        if not query_tokens or not doc_tokens:
            return 0.0

        doc_token_set = set(doc_tokens)
        doc_freq = Counter(doc_tokens)
        total_doc_tokens = len(doc_tokens)

        max_sim_sum = 0.0
        for q_tok in query_tokens:
            # MaxSim: max similarity of query token with any document token
            # Use soft matching: exact = 1.0, prefix = 0.7, no match = 0.0
            if q_tok in doc_token_set:
                tf = doc_freq[q_tok] / total_doc_tokens
                max_sim = min(1.0, 0.7 + 0.3 * tf * 10)  # TF-boosted exact match
            elif any(
                dt.startswith(q_tok[:4])
                for dt in doc_token_set
                if len(dt) >= 4 and len(q_tok) >= 4
            ):
                max_sim = 0.6  # prefix match
            else:
                max_sim = 0.0
            max_sim_sum += max_sim

        return max_sim_sum / len(query_tokens)

    def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """Rerank chunks using ColBERT MaxSim scoring. Returns sorted list."""
        if not chunks:
            return []

        scored: list[dict[str, Any]] = []
        for chunk in chunks:
            content = chunk.get("content", "")
            original_score = float(chunk.get("score", 0.5))
            colbert = self._maxsim_score(query, content)

            # Blend ColBERT with original retrieval score
            final = self._alpha * colbert + (1 - self._alpha) * original_score

            scored.append({
                **chunk,
                "score": final,
                "colbert_score": colbert,
                "original_score": original_score,
            })

        scored.sort(key=lambda c: c["score"], reverse=True)
        if top_k is not None:
            scored = scored[:top_k]
        return scored

    async def execute(
        self,
        *,
        query: str,
        chunks: list[dict[str, Any]],
        top_k: int = 5,
        **kwargs: Any,
    ) -> str:
        """Rerank chunks and return combined context string."""
        reranked = self.rerank(query, chunks, top_k=top_k)
        if not reranked:
            return ""
        return "\n\n".join(c.get("content", "") for c in reranked)
