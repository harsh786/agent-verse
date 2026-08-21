"""PolicyBundleSelector — produces a CompiledRuntimePolicy before graph execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.tenancy.context import TenantContext


@dataclass
class CompiledRuntimePolicy:
    allowed_capabilities: list[str] = field(default_factory=list)
    denied_capabilities: list[str] = field(default_factory=list)
    required_approvals: list[str] = field(default_factory=list)
    max_cost_usd: float = 10.0
    max_latency_ms: int = 120_000
    data_classes_allowed: list[str] = field(default_factory=lambda: ["public", "internal"])
    audit_level: str = "standard"
    compliance_constraints: list[str] = field(default_factory=list)


class PolicyBundleSelector:
    def select(
        self, profile: GoalRuntimeProfile, *, tenant_ctx: TenantContext
    ) -> CompiledRuntimePolicy:
        from app.policy_runtime.compiler import _compute_policy_fields

        fields = _compute_policy_fields(
            risk=profile.properties.risk,
            plan=tenant_ctx.plan,
            compliance=list(profile.security.compliance_tags),
            hitl=profile.security.hitl_required,
        )
        return CompiledRuntimePolicy(
            allowed_capabilities=[],
            denied_capabilities=fields["denied"],
            required_approvals=fields["approvals"],
            max_cost_usd=fields["max_cost"],
            audit_level=fields["audit"],
            data_classes_allowed=fields["data_classes"],
            compliance_constraints=list(profile.security.compliance_tags),
        )
