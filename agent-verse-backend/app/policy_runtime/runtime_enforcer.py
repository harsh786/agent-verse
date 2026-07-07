from __future__ import annotations
from app.policy_runtime.constraint_model import RuntimeConstraints


class RuntimeEnforcer:
    def is_capability_allowed(self, capability_id: str, constraints: RuntimeConstraints) -> bool:
        if capability_id in constraints.denied_capabilities:
            return False
        if constraints.allowed_capabilities:
            return capability_id in constraints.allowed_capabilities
        return True

    def requires_approval(self, constraints: RuntimeConstraints) -> bool:
        return bool(constraints.required_approvals)

    def check_cost(self, estimated_cost_usd: float, constraints: RuntimeConstraints) -> bool:
        return estimated_cost_usd <= constraints.max_cost_usd
