from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass
class SourceRef:
    source_type: str
    url: str = ""
    chunk_id: str = ""
    page_number: int | None = None
    tool_name: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "url": self.url,
            "chunk_id": self.chunk_id,
            "page_number": self.page_number,
            "tool_name": self.tool_name,
        }
