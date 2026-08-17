from __future__ import annotations

import re

from app.ingestion.chunkers.base import Chunk, ChunkerBase

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


class HeadingChunker(ChunkerBase):
    def chunk(self, content: str) -> list[Chunk]:
        positions = [(m.start(), m.group(1), m.group(2)) for m in _HEADING_PATTERN.finditer(content)]
        if not positions:
            return [Chunk(content=content.strip(), chunk_index=0, metadata={"heading": "document"})]
        chunks = []
        for i, (pos, hashes, heading_text) in enumerate(positions):
            end = positions[i+1][0] if i+1 < len(positions) else len(content)
            section_content = content[pos:end].strip()
            if section_content:
                chunks.append(Chunk(content=section_content, chunk_index=len(chunks),
                                    metadata={"heading": heading_text, "level": len(hashes), "section": heading_text}))
        return chunks
