from __future__ import annotations

from app.ingestion.chunkers.base import Chunk, ChunkerBase

_CHARS_PER_TOKEN = 4

class SemanticChunker(ChunkerBase):
    def __init__(self, max_chunk_tokens: int = 512) -> None:
        self._max_chars = max_chunk_tokens * _CHARS_PER_TOKEN

    def chunk(self, content: str) -> list[Chunk]:
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        if not paragraphs:
            return [Chunk(content=content.strip(), chunk_index=0)]
        chunks: list[Chunk] = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) <= self._max_chars:
                current = f"{current}\n\n{para}".strip()
            else:
                if current:
                    chunks.append(Chunk(content=current, chunk_index=len(chunks)))
                if len(para) > self._max_chars:
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
            if len(current) + len(s) <= self._max_chars:
                current = f"{current} {s}".strip()
            else:
                if current: parts.append(current)
                # If a single sentence still exceeds max_chars, split by words
                if len(s) > self._max_chars:
                    parts.extend(self._split_by_words(s))
                    current = ""
                else:
                    current = s
        if current: parts.append(current)
        return parts or [text]

    def _split_by_words(self, text: str) -> list[str]:
        words = text.split()
        parts: list[str] = []
        current = ""
        for word in words:
            if len(current) + len(word) + 1 <= self._max_chars:
                current = f"{current} {word}".strip()
            else:
                if current: parts.append(current)
                current = word
        if current: parts.append(current)
        return parts or [text]
