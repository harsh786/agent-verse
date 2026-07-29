"""Application-side Okapi BM25 corpus scoring."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any


def _tokenize(text: str) -> list[str]:
    """Tokenize complete text into lowercase Unicode-safe words."""

    return re.findall(r"[^\W_]+", text.casefold())


@dataclass
class BM25Hit:
    chunk_id: str
    content: str
    score: float
    source_metadata: dict[str, Any]


class BM25CorpusScorer:
    """Accumulate corpus statistics, then score documents without retaining them."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._document_frequency: Counter[str] = Counter()
        self._total_document_length = 0
        self.document_count = 0

    @property
    def average_document_length(self) -> float:
        return (
            self._total_document_length / self.document_count
            if self.document_count
            else 0.0
        )

    def observe(self, content: str) -> None:
        tokens = _tokenize(content)
        self.document_count += 1
        self._total_document_length += len(tokens)
        self._document_frequency.update(set(tokens))

    def score(self, query: str, content: str) -> float:
        return self.score_tokens(_tokenize(query), _tokenize(content))

    def score_tokens(self, query_tokens: list[str], document: list[str]) -> float:
        if not query_tokens or not document or not self.document_count:
            return 0.0
        frequencies = Counter(document)
        average_length = self.average_document_length or 1.0
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if not frequency:
                continue
            document_frequency = self._document_frequency[token]
            inverse_document_frequency = math.log(
                1.0
                + (self.document_count - document_frequency + 0.5)
                / (document_frequency + 0.5)
            )
            denominator = frequency + self._k1 * (
                1.0 - self._b + self._b * len(document) / average_length
            )
            score += (
                inverse_document_frequency
                * frequency
                * (self._k1 + 1.0)
                / denominator
            )
        return score


class BM25Retriever:
    """Okapi BM25 retrieval over a collection of chunks.

    k1=1.5 (term saturation), b=0.75 (length normalization) — standard defaults.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._chunks: list[dict[str, Any]] = []
        self._corpus: list[list[str]] = []
        self._scorer = BM25CorpusScorer(k1=k1, b=b)

    def index(self, chunks: list[dict[str, Any]]) -> None:
        """Index a list of chunk dicts (must have 'content' and 'chunk_id')."""
        self._chunks = [c for c in chunks if c.get("content")]
        self._corpus = [_tokenize(str(c["content"])) for c in self._chunks]
        self._scorer = BM25CorpusScorer(k1=self._k1, b=self._b)
        for chunk in self._chunks:
            self._scorer.observe(str(chunk["content"]))

    def search(self, query: str, top_k: int = 10) -> list[BM25Hit]:
        """Search indexed chunks using BM25 scoring.

        Returns up to *top_k* results with positive score in descending order.
        """
        if not self._chunks:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores = [
            self._scorer.score_tokens(query_tokens, document)
            for document in self._corpus
        ]

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
