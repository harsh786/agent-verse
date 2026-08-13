"""Governor-constrained decentralized swarm adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.orchestration.strategy_adapters import ExecutionTier


@dataclass(frozen=True, slots=True)
class DecentralizedSwarmAdapter:
    strategy_id: str = "decentralized_swarm"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> Any:
        from app.civilization.governor import Governor

        governor = kwargs.pop("governor", None)
        if governor is not None and not isinstance(governor, Governor):
            raise TypeError("swarm authority must be a Governor")
        return kwargs


__all__ = ["DecentralizedSwarmAdapter"]
