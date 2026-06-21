"""Permission matrix — per-tenant, per-tool action level configuration.

Action levels (ordered by escalating restriction):
  ALLOW       → execute silently
  ALLOW_LOG   → execute and record in audit trail (default for unconfigured tools)
  APPROVAL    → pause and request human approval via HITL gateway
  DENY        → block immediately, raise GovernanceError
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.tenancy.context import TenantContext


class ActionLevel(enum.StrEnum):
    ALLOW = "allow"
    ALLOW_LOG = "allow_log"
    APPROVAL = "approval"
    DENY = "deny"


@dataclass
class PermissionRule:
    tool_name: str
    level: ActionLevel
    daily_limit: int | None = None
    per_goal_limit: int | None = None
    scope_pattern: str | None = None


class PermissionMatrix:
    """In-memory per-tenant permission matrix.

    Default level for unconfigured tools is ALLOW_LOG (execute but audit).
    """

    _DEFAULT = ActionLevel.ALLOW_LOG

    def __init__(self) -> None:
        # Key: (tenant_id, tool_name) → PermissionRule
        self._rules: dict[tuple[str, str], PermissionRule] = {}

    def set_rule(self, rule: PermissionRule, *, tenant_ctx: TenantContext) -> None:
        self._rules[(tenant_ctx.tenant_id, rule.tool_name)] = rule

    def get_rule(self, tool_name: str, *, tenant_ctx: TenantContext) -> PermissionRule | None:
        return self._rules.get((tenant_ctx.tenant_id, tool_name))

    def check(self, tool_name: str, *, tenant_ctx: TenantContext) -> ActionLevel:
        rule = self.get_rule(tool_name, tenant_ctx=tenant_ctx)
        return rule.level if rule is not None else self._DEFAULT

    def list_rules(self, *, tenant_ctx: TenantContext) -> list[PermissionRule]:
        return [
            rule
            for (tid, _), rule in self._rules.items()
            if tid == tenant_ctx.tenant_id
        ]
