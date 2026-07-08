"""Parent-child chunking for high-precision retrieval with full-context return.

Strategy:
1. Split document into large parent chunks (e.g., 512 tokens)
2. Split each parent into small child chunks (e.g., 128 tokens)
3. Index only child chunks for retrieval (precise matching)
4. When a child is retrieved, return its parent for context

This combines the precision of small-chunk retrieval with the
context richness of large-chunk generation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParentChunk:
    chunk_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list["ChildChunk"] = field(default_factory=list)


@dataclass
class ChildChunk:
    chunk_id: str
    content: str
    parent_chunk_id: str
    window_start: int = 0  # Character position in parent
    window_end: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class ParentChildChunker:
    """Creates parent-child chunk hierarchy for retrieval."""

    def __init__(
        self,
        parent_chunk_size: int = 1500,  # ~500 tokens
        child_chunk_size: int = 400,    # ~130 tokens
        child_overlap: int = 50,
    ) -> None:
        self._parent_size = parent_chunk_size
        self._child_size = child_chunk_size
        self._child_overlap = child_overlap

    def chunk(self, content: str, document_id: str = "doc") -> list[ParentChunk]:
        """Split content into parent chunks, each with child chunks."""
        import uuid

        parents: list[ParentChunk] = []
        parent_texts = self._split_at_boundaries(content, self._parent_size)

        for p_idx, parent_text in enumerate(parent_texts):
            parent = ParentChunk(
                chunk_id=f"{document_id}_p{p_idx}_{uuid.uuid4().hex[:8]}",
                content=parent_text.strip(),
            )
            # Split parent into child chunks with overlap
            pos = 0
            c_idx = 0
            while pos < len(parent_text):
                end = min(pos + self._child_size, len(parent_text))
                child_text = parent_text[pos:end].strip()
                if child_text:
                    parent.children.append(
                        ChildChunk(
                            chunk_id=f"{parent.chunk_id}_c{c_idx}",
                            content=child_text,
                            parent_chunk_id=parent.chunk_id,
                            window_start=pos,
                            window_end=end,
                        )
                    )
                    c_idx += 1
                next_pos = end - self._child_overlap
                if next_pos <= pos or next_pos >= len(parent_text):
                    break
                pos = next_pos

            if not parent.children:
                parent.children = [
                    ChildChunk(
                        chunk_id=f"{parent.chunk_id}_c0",
                        content=parent.content,
                        parent_chunk_id=parent.chunk_id,
                        window_start=0,
                        window_end=len(parent.content),
                    )
                ]
            parents.append(parent)
        return parents

    def _split_at_boundaries(self, text: str, max_size: int) -> list[str]:
        """Split text at sentence boundaries respecting max_size."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for sent in sentences:
            if current_len + len(sent) > max_size and current:
                chunks.append(" ".join(current))
                current = [sent]
                current_len = len(sent)
            else:
                current.append(sent)
                current_len += len(sent)
        if current:
            chunks.append(" ".join(current))
        return chunks or [text]
