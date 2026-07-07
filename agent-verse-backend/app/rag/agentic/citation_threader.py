"""CitationThreader — attaches sequential citation indices to chunks."""
from __future__ import annotations
from typing import Any


class CitationThreader:
    def thread(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**c, "citation_index": i} for i, c in enumerate(chunks, start=1)]
