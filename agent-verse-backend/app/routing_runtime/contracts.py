"""Immutable routing decisions and measured outcomes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RoutingCategory = Literal["model", "skill", "tool", "embedding", "strategy"]


class RoutingSignalSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_shape: str
    complexity: str
    risk: str
    freshness: str
    requires_code: bool
    requires_collaboration: bool
    required_capabilities: frozenset[str]
    data_classes: frozenset[str]
    deadline_ms: int = Field(gt=0)
    max_cost_usd: float = Field(ge=0)
    max_tokens: int = Field(gt=0)
    tenant_plan: str
    profile_version: int = Field(gt=0)


class RoutingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    candidate_version: str
    provider: str
    capabilities: frozenset[str]
    readiness: Literal["ready", "degraded", "unavailable", "circuit_open"]
    trust_score: int = Field(ge=0, le=10_000)
    quality_score: int = Field(ge=0, le=10_000)
    estimated_cost_usd: float = Field(ge=0)
    estimated_latency_ms: int = Field(ge=0)
    saturation: int = Field(ge=0, le=10_000)
    policy_allowed: bool
    rejection_reasons: tuple[str, ...] = ()


class RoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str
    tenant_id: str
    goal_id: str
    execution_id: str
    category: RoutingCategory
    signals: RoutingSignalSet
    candidates: tuple[RoutingCandidate, ...]
    selected_candidate_id: str | None
    fallback_chain: tuple[str, ...]
    safe_rationale: str = Field(max_length=2_000)
    policy_trace: dict[str, Any]
    created_at: datetime

    @model_validator(mode="after")
    def selected_candidate_is_eligible(self) -> RoutingDecision:
        if self.selected_candidate_id is None:
            return self
        matches = [
            item for item in self.candidates if item.candidate_id == self.selected_candidate_id
        ]
        if len(matches) != 1 or matches[0].rejection_reasons:
            raise ValueError("selected routing candidate must be uniquely eligible")
        if self.fallback_chain and self.fallback_chain[0] != self.selected_candidate_id:
            raise ValueError("selected routing candidate must lead the fallback chain")
        return self


class OptimizationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome_id: str
    tenant_id: str
    decision_id: str
    attempt: int = Field(gt=0)
    evaluator_version: str
    success: bool
    quality_score: int = Field(ge=0, le=10_000)
    actual_cost_usd: float = Field(ge=0)
    actual_latency_ms: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    fallback_used: bool
    error_class: str | None = None
    recorded_at: datetime


__all__ = [
    "OptimizationOutcome",
    "RoutingCandidate",
    "RoutingCategory",
    "RoutingDecision",
    "RoutingSignalSet",
]
