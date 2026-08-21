"""CitationManager — threads source citations through retrieved context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Citation:
    index: int
    chunk_id: str
    source_url: str
    page_number: int | None
    score: float
    content_preview: str = ""


class CitationManager:
    def attach_citations(
        self, chunks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[Citation]]:
        citations = []
        annotated = []
        for i, chunk in enumerate(chunks, start=1):
            cit = Citation(
                index=i,
                chunk_id=chunk.get("chunk_id", ""),
                source_url=chunk.get("source_url", ""),
                page_number=chunk.get("page_number"),
                score=chunk.get("score", 0.0),
                content_preview=chunk.get("content", "")[:100],
            )
            citations.append(cit)
            annotated.append({**chunk, "_citation_index": i})
        return annotated, citations

    def format_citation_block(self, citations: list[Citation]) -> str:
        lines = ["Sources:"]
        for cit in citations:
            loc = f" p.{cit.page_number}" if cit.page_number else ""
            lines.append(f"[{cit.index}] {cit.source_url}{loc}")
        return "\n".join(lines)
