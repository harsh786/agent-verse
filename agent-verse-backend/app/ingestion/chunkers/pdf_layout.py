from __future__ import annotations
import re
from app.ingestion.chunkers.base import Chunk, ChunkerBase

_PAGE_MARKER = re.compile(r"---\s*PAGE\s*(\d+)\s*---", re.IGNORECASE)
_TABLE_PATTERN = re.compile(r"^\|.+\|", re.MULTILINE)


class PDFLayoutChunker(ChunkerBase):
    def chunk(self, content: str) -> list[Chunk]:
        pages = _PAGE_MARKER.split(content)
        if len(pages) > 1:
            chunks = []
            page_num = 1
            for i in range(0, len(pages), 2):
                page_content = pages[i].strip()
                if i + 1 < len(pages):
                    page_num = int(pages[i + 1])
                if page_content:
                    chunks.append(Chunk(content=page_content, chunk_index=len(chunks),
                                        metadata={"page_number": page_num, "section": f"page_{page_num}"}))
            return chunks or [Chunk(content=content.strip(), chunk_index=0, metadata={"page_number": 1})]
        paras = [p.strip() for p in content.split("\n\n") if p.strip()]
        chunks = []
        for i, para in enumerate(paras):
            meta: dict = {"section": f"section_{i+1}"}
            if _TABLE_PATTERN.search(para):
                meta["content_type"] = "table"
            chunks.append(Chunk(content=para, chunk_index=i, metadata=meta))
        return chunks or [Chunk(content=content.strip(), chunk_index=0, metadata={"page_number": 1})]
