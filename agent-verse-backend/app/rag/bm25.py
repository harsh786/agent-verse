"""Bounded application-side Okapi BM25 corpus scoring."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

_MAX_DOCUMENT_CHARS = 100_000
_MAX_DOCUMENT_TOKENS = 20_000


def _tokenize(text: str) -> list[str]:
    """Tokenize bounded text into lowercase Unicode-safe words."""

    return re.findall(r"[^\W_]+", text[:_MAX_DOCUMENT_CHARS].casefold())[
        :_MAX_DOCUMENT_TOKENS
    ]


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
        self._corpus: list[list[str]] = []
        self._document_frequency: Counter[str] = Counter()
        self._average_length = 0.0

    def index(self, chunks: list[dict[str, Any]]) -> None:
        """Index a list of chunk dicts (must have 'content' and 'chunk_id')."""
        self._chunks = [c for c in chunks if c.get("content")]
        self._corpus = [_tokenize(str(c["content"])) for c in self._chunks]
        self._document_frequency = Counter(
            token for document in self._corpus for token in set(document)
        )
        self._average_length = (
            sum(len(document) for document in self._corpus) / len(self._corpus)
            if self._corpus
            else 0.0
        )

    def search(self, query: str, top_k: int = 10) -> list[BM25Hit]:
        """Search indexed chunks using BM25 scoring.

        Returns up to *top_k* results with positive score in descending order.
        """
        if not self._chunks:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        document_count = len(self._corpus)
        average_length = self._average_length or 1.0
        scores: list[float] = []
        for document in self._corpus:
            frequencies = Counter(document)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies[token]
                if not frequency:
                    continue
                document_frequency = self._document_frequency[token]
                inverse_document_frequency = math.log(
                    1.0 + (document_count - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                denominator = frequency + self._k1 * (
                    1.0 - self._b + self._b * len(document) / average_length
                )
                score += inverse_document_frequency * frequency * (self._k1 + 1.0) / denominator
            scores.append(score)

        ranked = sorted(
            enumerate(scores),
            key=lambda item: (
                -item[1],
                str(self._chunks[item[0]].get("chunk_id", item[0])),
            ),
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
        """The built-in scorer has no optional runtime dependency."""

        return True
