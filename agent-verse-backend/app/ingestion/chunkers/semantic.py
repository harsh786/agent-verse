from __future__ import annotations

import re
from collections.abc import Callable

from app.agent.tokenizer import count_tokens
from app.ingestion.chunkers.base import Chunk, ChunkerBase

# Callable[[list[str]], list[list[float]]] — a synchronous embedding function.
EmbedFn = Callable[[list[str]], list[list[float]]]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticChunker(ChunkerBase):
    def __init__(
        self,
        max_chunk_tokens: int = 512,
        *,
        embed_fn: EmbedFn | None = None,
        similarity_threshold: float = 0.6,
    ) -> None:
        # ING-9: size by real tokens via the shared tokenizer, not char//4.
        self._max_tokens = max_chunk_tokens
        # ING-10: when a (sync) embedding function is injected, place chunk
        # boundaries at topic shifts (consecutive-sentence cosine below the
        # threshold) rather than purely by size. Falls back to greedy packing
        # when no embedder is available (the default), so existing callers are
        # unaffected. The chunker stays sync; callers pass a sync embed wrapper.
        self._embed_fn = embed_fn
        self._sim_threshold = similarity_threshold

    def chunk(self, content: str) -> list[Chunk]:
        if self._embed_fn is not None:
            boundary = self._embedding_boundary_chunk(content)
            if boundary is not None:
                return boundary
            # embed failure -> fall through to greedy packing (never crash)
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        if not paragraphs:
            return [Chunk(content=content.strip(), chunk_index=0)]
        chunks: list[Chunk] = []
        current = ""
        for para in paragraphs:
            combined = f"{current}\n\n{para}".strip() if current else para
            if count_tokens(combined) <= self._max_tokens:
                current = combined
            else:
                if current:
                    chunks.append(Chunk(content=current, chunk_index=len(chunks)))
                if count_tokens(para) > self._max_tokens:
                    for sent in self._split_sentences(para):
                        chunks.append(Chunk(content=sent, chunk_index=len(chunks)))
                    current = ""
                else:
                    current = para
        if current:
            chunks.append(Chunk(content=current, chunk_index=len(chunks)))
        return chunks or [Chunk(content=content.strip(), chunk_index=0)]

    def _sentences(self, text: str) -> list[str]:
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]

    def _embedding_boundary_chunk(self, content: str) -> list[Chunk] | None:
        """Split at topic shifts using consecutive-sentence cosine similarity.

        Returns None to signal the caller should fall back to greedy packing
        (empty/degenerate embeddings or an embed error) — never crashes.
        """
        text = content.strip()
        if not text:
            return []
        sentences = self._sentences(text)
        if len(sentences) <= 1:
            return [Chunk(content=text, chunk_index=0)]
        try:
            vecs = self._embed_fn(sentences)  # type: ignore[misc]
        except Exception:
            return None
        if not vecs or len(vecs) != len(sentences):
            return None
        chunks: list[Chunk] = []
        current: list[str] = [sentences[0]]
        for i in range(1, len(sentences)):
            sim = _cosine(vecs[i - 1], vecs[i])
            combined = " ".join([*current, sentences[i]])
            over_budget = count_tokens(combined) > self._max_tokens
            if sim < self._sim_threshold or over_budget:
                chunks.append(Chunk(content=" ".join(current), chunk_index=len(chunks)))
                current = [sentences[i]]
            else:
                current.append(sentences[i])
        if current:
            chunks.append(Chunk(content=" ".join(current), chunk_index=len(chunks)))
        return chunks

    def _split_sentences(self, text: str) -> list[str]:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        parts: list[str] = []
        current = ""
        for s in sentences:
            combined = f"{current} {s}".strip() if current else s
            if count_tokens(combined) <= self._max_tokens:
                current = combined
            else:
                if current:
                    parts.append(current)
                # If a single sentence still exceeds the budget, split by words
                if count_tokens(s) > self._max_tokens:
                    parts.extend(self._split_by_words(s))
                    current = ""
                else:
                    current = s
        if current:
            parts.append(current)
        return parts or [text]

    def _split_by_words(self, text: str) -> list[str]:
        words = text.split()
        parts: list[str] = []
        current = ""
        for word in words:
            combined = f"{current} {word}".strip() if current else word
            if count_tokens(combined) <= self._max_tokens:
                current = combined
            else:
                if current:
                    parts.append(current)
                current = word
        if current:
            parts.append(current)
        return parts or [text]
