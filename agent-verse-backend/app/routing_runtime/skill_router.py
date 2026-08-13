"""Canonical semantic, trusted, version-aware skill router."""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from app.routing_runtime.contracts import RoutingCandidate, RoutingDecision, RoutingSignalSet
from app.routing_runtime.router import route_candidates


class SkillCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    routing: RoutingCandidate
    semantic_score: int = Field(ge=0, le=10_000)
    allowed_tenant_ids: frozenset[str]
    compatible_versions: frozenset[str]
    token_cost: int = Field(ge=0)


class SkillRouter:
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
        candidates: tuple[SkillCandidate, ...],
        required_version: str,
    ) -> RoutingDecision:
        projected: list[RoutingCandidate] = []
        for item in candidates:
            reasons = list(item.routing.rejection_reasons)
            if tenant_id not in item.allowed_tenant_ids:
                reasons.append("tenant_denied")
            if required_version not in item.compatible_versions:
                reasons.append("version_incompatible")
            if item.token_cost > signals.max_tokens:
                reasons.append("token_budget")
            projected.append(
                item.routing.model_copy(
                    update={
                        "quality_score": (item.routing.quality_score + item.semantic_score) // 2,
                        "rejection_reasons": tuple(reasons),
                    }
                )
            )
        decision = route_candidates(
            tenant_id=tenant_id,
            goal_id=goal_id,
            execution_id=execution_id,
            category="skill",
            ordinal=ordinal,
            signals=signals,
            candidates=tuple(projected),
        )
        return cast(RoutingDecision, await self._store.save_decision(decision))


__all__ = ["SkillCandidate", "SkillRouter"]
