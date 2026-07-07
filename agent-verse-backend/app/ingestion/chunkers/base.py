from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Chunk:
    content: str
    chunk_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

class ChunkerBase(ABC):
    @abstractmethod
    def chunk(self, content: str) -> list[Chunk]: ...
