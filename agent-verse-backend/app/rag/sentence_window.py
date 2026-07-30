"""Sentence Window Retrieval — retrieve surrounding sentences for full context.

During retrieval, small sentence-level chunks are matched precisely.
Before returning to the LLM, the retrieved sentence is expanded to a
window of ±window_size surrounding sentences for richer context.

This is the "small-to-big" retrieval pattern from LlamaIndex.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()]


@dataclass
class SentenceWindowResult:
    """Retrieved sentence + surrounding window context."""

    sentence: str        # The matched sentence
    window: str          # Full context window (±window_size sentences)
    sentence_index: int  # Index within source text
    source_metadata: dict[str, Any]


class SentenceWindowChunker:
    """Ingestion-time: creates sentence-level chunks with window metadata."""

    def __init__(self, window_size: int = 2) -> None:
        """window_size: number of sentences before/after to include in retrieval."""
        self._window_size = window_size

    def chunk(
        self, text: str, metadata: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Split text into sentence chunks with window context stored in metadata."""
        import uuid

        sentences = _split_sentences(text)
        if not sentences:
            return [{"content": text, "chunk_id": uuid.uuid4().hex, "metadata": metadata or {}}]

        chunks: list[dict[str, Any]] = []
        for idx, sentence in enumerate(sentences):
            # Store the full window context in metadata for retrieval expansion
            start = max(0, idx - self._window_size)
            end = min(len(sentences), idx + self._window_size + 1)
            window = " ".join(sentences[start:end])

            chunks.append({
                "chunk_id": uuid.uuid4().hex,
                "content": sentence,  # Small sentence for precise matching
                "metadata": {
                    **(metadata or {}),
                    "window_context": window,    # Full context stored here
                    "sentence_index": idx,
                    "window_start": start,
                    "window_end": end,
                },
            })
        return chunks


class SentenceWindowRetriever:
    """Retrieval-time: expands retrieved sentences to their window context."""

    def expand(self, retrieval_results: list[Any]) -> list[Any]:
        """Replace sentence content with full window context when available."""
        expanded: list[Any] = []
        for result in retrieval_results:
            metadata = getattr(result, "source_metadata", {}) or {}
            window = metadata.get("window_context")
            if window and len(window) > len(getattr(result, "content", "")):
                # Expand to window context (better for LLM comprehension)
                result_copy = type(result)(
                    chunk_id=result.chunk_id,
                    content=window,
                    score=result.score,
                    source_metadata={
                        **metadata,
                        "original_sentence": result.content,
                        "window_expanded": True,
                    },
                    retrieval_legs=[
                        *getattr(result, "retrieval_legs", []),
                        "window_expanded",
                    ],
                )
                expanded.append(result_copy)
            else:
                expanded.append(result)
        return expanded
