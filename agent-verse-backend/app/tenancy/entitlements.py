"""Single entitlement check module.

Answers: "Can tenant T use feature F at volume V?"

This replaces scattered `if plan == "enterprise"` checks scattered throughout
the codebase with a single, testable, auditable place.
"""
from __future__ import annotations

from app.tenancy.context import PLAN_LIMITS, PlanTier, TenantContext

# Feature flags per plan
_PLAN_FEATURES: dict[PlanTier, set[str]] = {
    PlanTier.FREE: {
        "goals", "agents", "knowledge", "memory",
    },
    PlanTier.STARTER: {
        "goals", "agents", "knowledge", "memory",
        "marketplace", "templates",
        "byo_api_key",
    },
    PlanTier.PROFESSIONAL: {
        "goals", "agents", "knowledge", "memory",
        "marketplace", "templates",
        "byo_api_key", "byo_endpoints",
        "simulations", "rpa", "a2a",
        "advanced_guardrails", "audit_export",
        "civilization",
    },
    PlanTier.ENTERPRISE: {
        "goals", "agents", "knowledge", "memory",
        "marketplace", "templates",
        "byo_api_key", "byo_endpoints",
        "simulations", "rpa", "a2a",
        "advanced_guardrails", "audit_export",
        "civilization",
        "sso", "scim", "custom_roles", "white_label",
        "compliance_reports", "data_residency",
        "priority_support", "sla",
    },
}


def has_feature(tenant_ctx: TenantContext, feature: str) -> bool:
    """Return True if the tenant's plan includes the given feature."""
    allowed = _PLAN_FEATURES.get(tenant_ctx.plan, set())
    # Enterprise inherits all lower-tier features
    if tenant_ctx.plan == PlanTier.ENTERPRISE:
        return True
    return feature in allowed


def check_limit(
    tenant_ctx: TenantContext,
    resource: str,
    current_count: int,
) -> tuple[bool, int]:
    """Check if adding one more resource is within plan limits.

    Returns (allowed: bool, limit: int).
    resource: "agents" | "api_keys" | "knowledge_collections"
    """
    limits = PLAN_LIMITS.get(tenant_ctx.plan)
    if limits is None:
        return False, 0

    limit_map = {
        "agents": limits.max_agents,
        "api_keys": limits.max_api_keys,
        "knowledge_collections": limits.max_knowledge_collections,
    }
    limit = limit_map.get(resource, 0)
    return current_count < limit, limit


def assert_feature(tenant_ctx: TenantContext, feature: str) -> None:
    """Raise PermissionError if tenant's plan doesn't include the feature."""
    if not has_feature(tenant_ctx, feature):
        raise PermissionError(
            f"Feature '{feature}' is not available on the "
            f"'{tenant_ctx.plan.value}' plan. Upgrade to access this feature."
        )


def assert_limit(
    tenant_ctx: TenantContext,
    resource: str,
    current_count: int,
) -> None:
    """Raise PermissionError if adding one more resource would exceed plan limits."""
    allowed, limit = check_limit(tenant_ctx, resource, current_count)
    if not allowed:
        raise PermissionError(
            f"Plan limit reached: {tenant_ctx.plan.value} allows {limit} {resource}. "
            f"Current count: {current_count}. Upgrade to increase limits."
        )
