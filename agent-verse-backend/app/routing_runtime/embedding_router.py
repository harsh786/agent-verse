"""Canonical embedding routing with explicit lexical degradation."""

from __future__ import annotations

from typing import Any, cast

from app.routing_runtime.contracts import RoutingCandidate, RoutingDecision, RoutingSignalSet
from app.routing_runtime.router import route_candidates


class EmbeddingRouter:
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
        allow_lexical_fallback: bool,
    ) -> RoutingDecision:
        decision = route_candidates(
            tenant_id=tenant_id,
            goal_id=goal_id,
            execution_id=execution_id,
            category="embedding",
            ordinal=ordinal,
            signals=signals,
            candidates=candidates,
        )
        if decision.selected_candidate_id is None and allow_lexical_fallback:
            lexical = RoutingCandidate(
                candidate_id="lexical",
                candidate_version="v1",
                provider="local",
                capabilities=signals.required_capabilities,
                readiness="degraded",
                trust_score=10_000,
                quality_score=1,
                estimated_cost_usd=0,
                estimated_latency_ms=0,
                saturation=0,
                policy_allowed=True,
            )
            decision = route_candidates(
                tenant_id=tenant_id,
                goal_id=goal_id,
                execution_id=execution_id,
                category="embedding",
                ordinal=ordinal,
                signals=signals,
                candidates=(*candidates, lexical),
                policy_trace={"degraded_to": "lexical"},
            )
        return cast(RoutingDecision, await self._store.save_decision(decision))


__all__ = ["EmbeddingRouter"]
