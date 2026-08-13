from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.routing_runtime.contracts import (
    OptimizationOutcome,
    RoutingCandidate,
    RoutingDecision,
    RoutingSignalSet,
)


def signals() -> RoutingSignalSet:
    return RoutingSignalSet(
        task_shape="analysis",
        complexity="medium",
        risk="low",
        freshness="current",
        requires_code=False,
        requires_collaboration=False,
        required_capabilities=frozenset({"structured"}),
        data_classes=frozenset({"internal"}),
        deadline_ms=1000,
        max_cost_usd=1,
        max_tokens=1000,
        tenant_plan="professional",
        profile_version=1,
    )


def candidate(identifier: str, *, rejected: bool = False) -> RoutingCandidate:
    return RoutingCandidate(
        candidate_id=identifier,
        candidate_version="v1",
        provider="provider",
        capabilities=frozenset({"structured"}),
        readiness="ready",
        trust_score=9000,
        quality_score=9000,
        estimated_cost_usd=0.1,
        estimated_latency_ms=100,
        saturation=0,
        policy_allowed=not rejected,
        rejection_reasons=("policy_denied",) if rejected else (),
    )


def decision() -> RoutingDecision:
    return RoutingDecision(
        decision_id="decision",
        tenant_id="tenant",
        goal_id="goal",
        execution_id="execution",
        category="model",
        signals=signals(),
        candidates=(candidate("a"), candidate("b")),
        selected_candidate_id="a",
        fallback_chain=("a", "b"),
        safe_rationale="eligible candidates ranked deterministically",
        policy_trace={},
        created_at=datetime.now(UTC),
    )


def test_decision_rejects_selected_ineligible_candidate() -> None:
    with pytest.raises(ValidationError):
        decision().model_copy(
            update={"candidates": (candidate("a", rejected=True),)}, deep=True
        ).model_validate(
            {
                **decision().model_dump(),
                "candidates": [candidate("a", rejected=True).model_dump()],
                "fallback_chain": ["a"],
            }
        )


def test_outcome_rejects_negative_measurements() -> None:
    with pytest.raises(ValidationError):
        OptimizationOutcome(
            outcome_id="o",
            tenant_id="tenant",
            decision_id="decision",
            attempt=1,
            evaluator_version="v1",
            success=False,
            quality_score=0,
            actual_cost_usd=-1,
            actual_latency_ms=0,
            prompt_tokens=0,
            completion_tokens=0,
            fallback_used=False,
            recorded_at=datetime.now(UTC),
        )
