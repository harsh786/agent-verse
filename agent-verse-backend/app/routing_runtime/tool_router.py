"""Canonical tool routing with immediate dispatch reauthorization."""

from __future__ import annotations

from typing import Any, cast

from app.routing_runtime.contracts import RoutingCandidate, RoutingDecision, RoutingSignalSet
from app.routing_runtime.router import route_candidates


class ToolRouter:
    def __init__(self, *, decision_store: Any, authorizer: Any) -> None:
        self._store = decision_store
        self._authorizer = authorizer

    async def route(
        self,
        *,
        tenant_id: str,
        goal_id: str,
        execution_id: str,
        ordinal: int,
        signals: RoutingSignalSet,
        candidates: tuple[RoutingCandidate, ...],
    ) -> RoutingDecision:
        projected: list[RoutingCandidate] = []
        for item in candidates:
            projected.append(
                item.model_copy(
                    update={
                        "policy_allowed": item.policy_allowed
                        and bool(await self._authorizer(tenant_id, item.candidate_id))
                    }
                )
            )
        decision = route_candidates(
            tenant_id=tenant_id,
            goal_id=goal_id,
            execution_id=execution_id,
            category="tool",
            ordinal=ordinal,
            signals=signals,
            candidates=tuple(projected),
        )
        return cast(RoutingDecision, await self._store.save_decision(decision))

    async def reauthorize_before_dispatch(self, tenant_id: str, tool_id: str) -> None:
        if not await self._authorizer(tenant_id, tool_id):
            raise PermissionError("tool authorization changed before dispatch")


__all__ = ["ToolRouter"]
