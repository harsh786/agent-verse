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
        requests_per_minute=10_000,       # unlimited for local dev
        goals_per_day=100_000,            # unlimited for local dev
        max_agents=1_000,                 # unlimited for local dev
        max_api_keys=100,                 # unlimited for local dev
        max_knowledge_collections=100,    # unlimited for local dev
        goal_timeout_seconds=86400,       # 24 hours
    ),
    PlanTier.STARTER: PlanLimits(
        requests_per_minute=10_000,
        goals_per_day=100_000,
        max_agents=1_000,
        max_api_keys=100,
        max_knowledge_collections=100,
        goal_timeout_seconds=86400,       # 24 hours
    ),
    PlanTier.PROFESSIONAL: PlanLimits(
        requests_per_minute=10_000,
        goals_per_day=100_000,
        max_agents=1_000,
        max_api_keys=100,
        max_knowledge_collections=100,
        goal_timeout_seconds=86400,       # 24 hours
    ),
    PlanTier.ENTERPRISE: PlanLimits(
        requests_per_minute=10_000,
        goals_per_day=100_000,
        max_agents=1_000,
        max_api_keys=100,
        max_knowledge_collections=100,
        goal_timeout_seconds=86400,       # 24 hours
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
