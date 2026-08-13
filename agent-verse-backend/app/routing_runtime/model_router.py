"""Canonical async model router."""

from __future__ import annotations

from typing import Any, cast

from app.routing_runtime.contracts import RoutingCandidate, RoutingDecision, RoutingSignalSet
from app.routing_runtime.router import route_candidates


class ModelRouter:
    def __init__(self, *, decision_store: Any) -> None:
        self._store = decision_store

    async def route(
        self,
        *,
        tenant_id: str,
        goal_id: str,
        execution_id: str,
        ordinal: int,
        signals: RoutingSignalSet,
        candidates: tuple[RoutingCandidate, ...],
        minimum_quality: int = 0,
        policy_trace: dict[str, Any] | None = None,
    ) -> RoutingDecision:
        decision = route_candidates(
            tenant_id=tenant_id,
            goal_id=goal_id,
            execution_id=execution_id,
            category="model",
            ordinal=ordinal,
            signals=signals,
            candidates=candidates,
            minimum_quality=minimum_quality,
            policy_trace=policy_trace,
        )
        return cast(RoutingDecision, await self._store.save_decision(decision))


__all__ = ["ModelRouter"]
