"""True Okapi BM25 retrieval using rank_bm25 library.

This replaces the PostgreSQL FTS approximation with proper BM25 scoring:
  - IDF normalization (Okapi BM25 parameter k1=1.5, b=0.75)
  - Works on in-memory chunk collections
  - Used as an additional leg in hybrid retrieval when rank_bm25 is available
  - Falls back gracefully to simple TF scoring when library is unavailable
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric words."""
    return re.findall(r"\b[a-zA-Z0-9]+\b", text.lower())


@dataclass
class BM25Hit:
    chunk_id: str
    content: str
    score: float
    source_metadata: dict[str, Any]


class BM25Retriever:
    """Okapi BM25 retrieval over a collection of chunks.

    k1=1.5 (term saturation), b=0.75 (length normalization) — standard defaults.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._chunks: list[dict[str, Any]] = []
        self._bm25: Any = None

    def index(self, chunks: list[dict[str, Any]]) -> None:
        """Index a list of chunk dicts (must have 'content' and 'chunk_id')."""
        self._chunks = [c for c in chunks if c.get("content")]
        corpus = [_tokenize(c["content"]) for c in self._chunks]
        if not corpus:
            self._bm25 = None
            return
        try:
            from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]

            self._bm25 = BM25Okapi(corpus, k1=self._k1, b=self._b)
        except ImportError:
            self._bm25 = None

    def search(self, query: str, top_k: int = 10) -> list[BM25Hit]:
        """Search indexed chunks using BM25 scoring.

        Returns up to *top_k* results with positive score in descending order.
        """
        if not self._chunks:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        if self._bm25 is not None:
            scores: list[float] = list(self._bm25.get_scores(query_tokens))
        else:
            # Fallback: simple term-frequency scoring
            scores = [
                float(
                    sum(
                        1
                        for qt in query_tokens
                        if qt in _tokenize(c.get("content", ""))
                    )
                )
                for c in self._chunks
            ]

        ranked = sorted(
            enumerate(scores),
            key=lambda x: x[1],
            reverse=True,
        )
        results: list[BM25Hit] = []
        for idx, score in ranked[:top_k]:
            if score <= 0:
                break
            chunk = self._chunks[idx]
            results.append(
                BM25Hit(
                    chunk_id=chunk.get("chunk_id", f"bm25_{idx}"),
                    content=chunk.get("content", ""),
                    score=float(score),
                    source_metadata=chunk.get(
                        "source_metadata", chunk.get("metadata", {})
                    ),
                )
            )
        return results

    @property
    def is_available(self) -> bool:
        """True if rank_bm25 library is installed."""
        try:
            import rank_bm25  # noqa: F401  # type: ignore[import-untyped]

            return True
        except ImportError:
            return False
