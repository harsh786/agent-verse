"""Base classes for agent pattern adapters."""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from typing import Any


class PatternState(enum.StrEnum):
    IMPLEMENTED = "implemented"
    PARTIAL = "partial"
    PLANNED = "planned"
    DISABLED = "disabled"


class AgentPattern(ABC):
    @property
    @abstractmethod
    def pattern_id(self) -> str: ...

    @property
    def state(self) -> PatternState:
        return PatternState.PLANNED

    @property
    def description(self) -> str:
        return ""

    @property
    def node_name(self) -> str:
        return ""

    def get_node_config(self, pattern_config: Any) -> dict[str, Any]:
        return {}

    def is_compatible(self, goal_properties: Any) -> bool:
        return True
