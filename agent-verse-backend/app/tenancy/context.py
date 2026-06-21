"""Tenant identity, plan tiers, and per-plan limits."""

from __future__ import annotations

import enum
from dataclasses import dataclass


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


PLAN_LIMITS: dict[PlanTier, PlanLimits] = {
    PlanTier.FREE: PlanLimits(
        requests_per_minute=60,
        goals_per_day=10,
        max_agents=2,
        max_api_keys=2,
        max_knowledge_collections=1,
    ),
    PlanTier.STARTER: PlanLimits(
        requests_per_minute=300,
        goals_per_day=100,
        max_agents=10,
        max_api_keys=5,
        max_knowledge_collections=5,
    ),
    PlanTier.PROFESSIONAL: PlanLimits(
        requests_per_minute=1200,
        goals_per_day=1000,
        max_agents=50,
        max_api_keys=20,
        max_knowledge_collections=20,
    ),
    PlanTier.ENTERPRISE: PlanLimits(
        requests_per_minute=6000,
        goals_per_day=10000,
        max_agents=500,
        max_api_keys=100,
        max_knowledge_collections=100,
    ),
}


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Immutable identity injected into every authenticated request."""

    tenant_id: str
    plan: PlanTier
    api_key_id: str
