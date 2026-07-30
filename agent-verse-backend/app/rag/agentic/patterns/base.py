"""Base class for RAG pattern adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any


class RAGPatternState(StrEnum):
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
