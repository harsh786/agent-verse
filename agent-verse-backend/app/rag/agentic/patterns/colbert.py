"""ColBERT — Late Interaction Reranking via MaxSim Token Scoring.

Production path: Uses all-MiniLM-L6-v2 from sentence-transformers to generate
token-level embeddings, then computes MaxSim exactly as in ColBERT.

Fallback: TF-IDF weighted token overlap (no external model required).
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
    "should", "may", "might", "must", "shall", "can", "of", "in", "on",
    "at", "to", "for", "with", "by", "from", "as", "and", "or", "but",
    "not", "so", "if", "when", "where", "how", "that", "this", "these",
    "those", "it", "its", "i", "you", "he", "she", "we", "they", "what",
    "who", "which",
})

_encoder_instance: Any = None


def _get_encoder() -> Any | None:
    """Load sentence-transformers encoder lazily (singleton)."""
    global _encoder_instance
    if _encoder_instance is not None:
        return _encoder_instance
    try:
        from sentence_transformers import SentenceTransformer

        # all-MiniLM-L6-v2: 22MB, 384-dim, fast and accurate
        _encoder_instance = SentenceTransformer("all-MiniLM-L6-v2")
        return _encoder_instance
    except Exception:
        return None


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase non-stopword tokens."""
    tokens = re.findall(r'\b[a-zA-Z0-9]+\b', text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two float vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    return dot / (mag_a * mag_b) if mag_a > 0 and mag_b > 0 else 0.0


def _maxsim_with_embeddings(
    query_emb: list[list[float]],
    doc_emb: list[list[float]],
) -> float:
    """True ColBERT MaxSim using token embeddings."""
    if not query_emb or not doc_emb:
        return 0.0
    total = 0.0
    for q_tok_emb in query_emb:
        max_sim = max((_cosine(q_tok_emb, d_tok_emb) for d_tok_emb in doc_emb), default=0.0)
        total += max_sim
    return total / len(query_emb)


@dataclass
class ColBERTScore:
    chunk_id: str
    content: str
    colbert_score: float
    original_score: float


class ColBERTPattern(RAGPattern):
    """ColBERT late interaction reranking.

    Production: token-level embeddings via all-MiniLM-L6-v2 + MaxSim.
    Fallback: TF-IDF weighted token overlap.
    """

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
        encoder = _get_encoder()
        backend = (
            "all-MiniLM-L6-v2 (real token embeddings)"
            if encoder
            else "TF-IDF token overlap (fallback)"
        )
        return (
            f"ColBERT late interaction reranking — MaxSim token-level scoring. "
            f"Backend: {backend}. Alpha={self._alpha}."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        try:
            from app.core.config import get_settings

            if not get_settings().enable_colbert:
                return False
        except Exception:
            pass
        return True

    def _embed_tokens(self, text: str) -> list[list[float]] | None:
        """Embed each token of the text.

        Returns list of per-token embeddings (shape: [n_tokens, dim]) or None
        when the encoder is unavailable.
        """
        encoder = _get_encoder()
        if encoder is None:
            return None
        tokens = _tokenize(text)
        if not tokens:
            return None
        try:
            embeddings = encoder.encode(tokens, batch_size=64, show_progress_bar=False)
            return [emb.tolist() for emb in embeddings]
        except Exception:
            return None

    def _maxsim_score(self, query: str, document: str) -> float:
        """Compute MaxSim score — real embeddings or TF-IDF fallback."""
        # Try real token embeddings first
        q_emb = self._embed_tokens(query)
        d_emb = self._embed_tokens(document)
        if q_emb is not None and d_emb is not None:
            return _maxsim_with_embeddings(q_emb, d_emb)

        # TF-IDF fallback (original implementation)
        query_tokens = _tokenize(query)
        doc_tokens = _tokenize(document)
        if not query_tokens or not doc_tokens:
            return 0.0
        doc_token_set = set(doc_tokens)
        doc_freq = Counter(doc_tokens)
        total_doc_tokens = len(doc_tokens)
        max_sim_sum = 0.0
        for q_tok in query_tokens:
            if q_tok in doc_token_set:
                tf = doc_freq[q_tok] / total_doc_tokens
                max_sim = min(1.0, 0.7 + 0.3 * tf * 10)
            elif any(
                dt.startswith(q_tok[:4])
                for dt in doc_token_set
                if len(dt) >= 4 and len(q_tok) >= 4
            ):
                max_sim = 0.6
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
        import asyncio

        try:
            from app.observability.logging import get_logger

            get_logger(__name__).info("colbert_late_interaction_started", query=query[:60])
        except Exception:
            pass

        # Run blocking inference in thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        try:
            reranked = await loop.run_in_executor(None, self.rerank, query, chunks, top_k)
        except Exception:
            reranked = self.rerank(query, chunks, top_k)

        result = "\n\n".join(c.get("content", "") for c in reranked) if reranked else ""

        try:
            from app.observability.logging import get_logger

            get_logger(__name__).info("colbert_late_interaction_completed", result_len=len(result))
        except Exception:
            pass

        return result
