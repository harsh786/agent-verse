"""Shared deterministic eligibility and ranking engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.routing_runtime.contracts import (
    RoutingCandidate,
    RoutingCategory,
    RoutingDecision,
    RoutingSignalSet,
)


def route_candidates(
    *,
    tenant_id: str,
    goal_id: str,
    execution_id: str,
    category: RoutingCategory,
    ordinal: int,
    signals: RoutingSignalSet,
    candidates: tuple[RoutingCandidate, ...],
    minimum_quality: int = 0,
    maximum_saturation: int = 9_000,
    policy_trace: dict[str, Any] | None = None,
) -> RoutingDecision:
    evaluated: list[RoutingCandidate] = []
    scores: dict[str, int] = {}
    for candidate in candidates:
        reasons = list(candidate.rejection_reasons)
        if candidate.readiness not in {"ready", "degraded"}:
            reasons.append(candidate.readiness)
        if not candidate.policy_allowed:
            reasons.append("policy_denied")
        if not signals.required_capabilities <= candidate.capabilities:
            reasons.append("missing_capability")
        if candidate.quality_score < minimum_quality:
            reasons.append("quality_floor")
        if candidate.estimated_cost_usd > signals.max_cost_usd:
            reasons.append("cost_ceiling")
        if candidate.estimated_latency_ms > signals.deadline_ms:
            reasons.append("deadline_exceeded")
        if candidate.saturation > maximum_saturation:
            reasons.append("saturated")
        accepted = candidate.model_copy(update={"rejection_reasons": tuple(sorted(set(reasons)))})
        evaluated.append(accepted)
        if not accepted.rejection_reasons:
            cost_penalty = int(
                (accepted.estimated_cost_usd / max(signals.max_cost_usd, 0.000001)) * 2_000
            )
            latency_penalty = accepted.estimated_latency_ms * 2_000 // signals.deadline_ms
            scores[accepted.candidate_id] = (
                accepted.quality_score * 5
                + accepted.trust_score * 3
                - cost_penalty
                - latency_penalty
                - accepted.saturation
            )
    ranked = tuple(sorted(scores, key=lambda identifier: (-scores[identifier], identifier)))
    decision_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{tenant_id}:{execution_id}:{category}:{signals.profile_version}:{ordinal}",
    ).hex
    return RoutingDecision(
        decision_id=decision_id,
        tenant_id=tenant_id,
        goal_id=goal_id,
        execution_id=execution_id,
        category=category,
        signals=signals,
        candidates=tuple(evaluated),
        selected_candidate_id=ranked[0] if ranked else None,
        fallback_chain=ranked,
        safe_rationale=(
            "Eligible candidates ranked by quality, trust, cost, latency, and saturation"
            if ranked
            else "No candidate satisfied hard routing constraints"
        ),
        policy_trace=policy_trace or {},
        created_at=datetime.now(UTC),
    )


__all__ = ["route_candidates"]
