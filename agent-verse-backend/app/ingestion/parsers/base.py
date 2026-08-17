from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedChunk:
    content: str
    chunk_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"content": self.content, "chunk_index": self.chunk_index, **self.metadata}
