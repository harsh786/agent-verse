"""Base class for RAG pattern adapters."""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from typing import Any


class RAGPatternState(str, enum.Enum):
    IMPLEMENTED = "implemented"
    PARTIAL = "partial"
    PLANNED = "planned"


class RAGPattern(ABC):
    @property
    @abstractmethod
    def pattern_id(self) -> str: ...

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.PLANNED

    @property
    def description(self) -> str:
        return ""

    def is_compatible(self, goal_properties: Any) -> bool:
        return True
