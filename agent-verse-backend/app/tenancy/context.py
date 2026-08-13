"""Tenant identity, plan tiers, and per-plan limits."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class PlanTier(enum.StrEnum):
    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


@dataclass(frozen=True, slots=True)
class PlanLimits:
    requests_per_minute: int
    goals_per_day: int
    max_agents: int
    max_api_keys: int
    max_knowledge_collections: int
    goal_timeout_seconds: int


PLAN_LIMITS: dict[PlanTier, PlanLimits] = {
    PlanTier.FREE: PlanLimits(
        requests_per_minute=60,
        goals_per_day=25,
        max_agents=3,
        max_api_keys=2,
        max_knowledge_collections=1,
        goal_timeout_seconds=3600,
    ),
    PlanTier.STARTER: PlanLimits(
        requests_per_minute=120,
        goals_per_day=100,
        max_agents=10,
        max_api_keys=5,
        max_knowledge_collections=10,
        goal_timeout_seconds=7200,
    ),
    PlanTier.PROFESSIONAL: PlanLimits(
        requests_per_minute=600,
        goals_per_day=1_000,
        max_agents=50,
        max_api_keys=20,
        max_knowledge_collections=50,
        goal_timeout_seconds=28_800,
    ),
    PlanTier.ENTERPRISE: PlanLimits(
        requests_per_minute=10_000,
        goals_per_day=50_000,
        max_agents=1_000,
        max_api_keys=100,
        max_knowledge_collections=200,
        goal_timeout_seconds=86_400,
    ),
}


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Immutable identity injected into every authenticated request."""

    tenant_id: str
    plan: PlanTier
    api_key_id: str
    # RBAC roles assigned to this API key / SSO user (expanded from role hierarchy)
    roles: tuple[str, ...] = field(default_factory=tuple)
