from __future__ import annotations

from app.agent.tokenizer import count_tokens
from app.ingestion.chunkers.base import Chunk, ChunkerBase


class SemanticChunker(ChunkerBase):
    def __init__(self, max_chunk_tokens: int = 512) -> None:
        # ING-9: size by real tokens via the shared tokenizer, not char//4.
        self._max_tokens = max_chunk_tokens

    def chunk(self, content: str) -> list[Chunk]:
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

    def _split_sentences(self, text: str) -> list[str]:
        import re

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
