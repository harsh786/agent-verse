"""Semantic text chunker with sentence-boundary, code-aware, and markdown-aware chunking.

Pure Python — no external dependencies required.
Uses regex and simple heuristics for sentence detection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chunk:
    content: str
    start_char: int
    end_char: int
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.content)


class SemanticChunker:
    """Chunks text into semantically meaningful pieces.
    
    Strategies:
    - 'text': sentence-boundary aware chunking with overlap
    - 'markdown': split on headings (# ##)
    - 'code': split on function/class definitions
    - 'fixed': fixed-size with overlap (fallback)
    """

    def __init__(
        self,
        max_chars: int = 512,
        overlap_chars: int = 64,
        min_chunk_chars: int = 50,
    ) -> None:
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars
        self.min_chunk_chars = min_chunk_chars

    def chunk(self, text: str, source_type: str = "text") -> list[Chunk]:
        """Chunk text according to source type."""
        if not text.strip():
            return []
        if source_type == "markdown":
            return self._chunk_markdown(text)
        if source_type in {"python", "typescript", "javascript", "code"}:
            return self._chunk_code(text)
        return self._chunk_sentences(text)

    def _chunk_sentences(self, text: str) -> list[Chunk]:
        """Split on sentence boundaries, respect max_chars."""
        # Split on sentence-ending punctuation
        sentence_pattern = re.compile(r'(?<=[.!?])\s+')
        sentences = sentence_pattern.split(text)
        
        chunks: list[Chunk] = []
        current = ""
        current_start = 0
        char_pos = 0
        
        for sentence in sentences:
            if len(current) + len(sentence) + 1 <= self.max_chars:
                if current:
                    current += " " + sentence
                else:
                    current = sentence
                    current_start = char_pos
            else:
                if current and len(current) >= self.min_chunk_chars:
                    chunks.append(Chunk(
                        content=current.strip(),
                        start_char=current_start,
                        end_char=current_start + len(current),
                    ))
                    # Overlap: take last overlap_chars from current
                    if self.overlap_chars > 0 and len(current) > self.overlap_chars:
                        overlap = current[-self.overlap_chars:]
                        current = overlap + " " + sentence
                    else:
                        current = sentence
                    current_start = char_pos
                else:
                    current = sentence
                    current_start = char_pos
            char_pos += len(sentence) + 1
        
        if current.strip() and len(current.strip()) >= self.min_chunk_chars:
            chunks.append(Chunk(
                content=current.strip(),
                start_char=current_start,
                end_char=current_start + len(current),
            ))
        
        return chunks

    def _chunk_markdown(self, text: str) -> list[Chunk]:
        """Split on markdown headings, then recursively chunk large sections."""
        heading_pattern = re.compile(r'^#{1,3}\s+.+$', re.MULTILINE)
        sections = heading_pattern.split(text)
        headings = heading_pattern.findall(text)
        
        chunks: list[Chunk] = []
        char_offset = 0
        
        for i, section in enumerate(sections):
            heading = headings[i - 1] if i > 0 else ""
            full_section = f"{heading}\n{section}".strip() if heading else section.strip()
            
            if not full_section:
                char_offset += len(section) + len(heading) + 1
                continue
            
            if len(full_section) <= self.max_chars:
                if len(full_section) >= self.min_chunk_chars:
                    chunks.append(Chunk(
                        content=full_section,
                        start_char=char_offset,
                        end_char=char_offset + len(full_section),
                        metadata={"heading": heading},
                    ))
            else:
                # Recursively chunk large sections
                sub_chunks = self._chunk_sentences(full_section)
                for sc in sub_chunks:
                    sc.start_char += char_offset
                    sc.end_char += char_offset
                    sc.metadata["heading"] = heading
                    chunks.append(sc)
            
            char_offset += len(section) + len(heading) + 1
        
        return chunks

    def _chunk_code(self, text: str) -> list[Chunk]:
        """Split Python/TS code on function/class definitions."""
        # Patterns for Python and TypeScript
        def_pattern = re.compile(
            r'^(?:async\s+)?(?:def|class|function|const\s+\w+\s*=|export\s+(?:async\s+)?function)',
            re.MULTILINE
        )
        
        parts = def_pattern.split(text)
        separators = def_pattern.findall(text)
        
        chunks: list[Chunk] = []
        char_offset = 0
        
        for i, part in enumerate(parts):
            sep = separators[i - 1] if i > 0 else ""
            full_part = f"{sep}{part}".strip() if sep else part.strip()
            
            if not full_part:
                char_offset += len(part) + len(sep)
                continue
            
            if len(full_part) <= self.max_chars * 2:  # Allow 2x for code
                if len(full_part) >= self.min_chunk_chars:
                    chunks.append(Chunk(
                        content=full_part,
                        start_char=char_offset,
                        end_char=char_offset + len(full_part),
                    ))
            else:
                # Large function/class — chunk by lines
                lines = full_part.split("\n")
                current_lines: list[str] = []
                for line in lines:
                    current_lines.append(line)
                    block = "\n".join(current_lines)
                    if len(block) > self.max_chars:
                        if len(block) >= self.min_chunk_chars:
                            chunks.append(Chunk(
                                content=block,
                                start_char=char_offset,
                                end_char=char_offset + len(block),
                            ))
                        current_lines = [line]
                if current_lines:
                    block = "\n".join(current_lines)
                    if len(block) >= self.min_chunk_chars:
                        chunks.append(Chunk(content=block, start_char=char_offset,
                                            end_char=char_offset + len(block)))
            
            char_offset += len(part) + len(sep)
        
        return chunks
