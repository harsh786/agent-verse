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

    def check(
        self,
        tool_name: str,
        *,
        tenant_ctx: TenantContext,
        scope_value: str | None = None,
    ) -> ActionLevel:
        """Return the action level for a tool call.

        If scope_value is provided and the rule has a scope_pattern, the value
        is matched against the pattern using glob semantics. A mismatch returns DENY.
        If scope_value is None, scope_pattern is not evaluated.
        """
        import fnmatch
        rule = self.get_rule(tool_name, tenant_ctx=tenant_ctx)
        if rule is None:
            return self._DEFAULT
        if scope_value is not None and rule.scope_pattern is not None:
            if not fnmatch.fnmatch(scope_value, rule.scope_pattern):
                return ActionLevel.DENY
        return rule.level

    def check_with_limits(
        self,
        tool_name: str,
        *,
        tenant_ctx: TenantContext,
        scope_value: str | None = None,
        daily_call_count: int = 0,
        goal_call_count: int = 0,
    ) -> ActionLevel:
        """Check permission plus rate limits.

        Evaluates scope, daily_limit, and per_goal_limit in order.
        Returns DENY as soon as any constraint is violated.
        """
        base = self.check(tool_name, tenant_ctx=tenant_ctx, scope_value=scope_value)
        if base == ActionLevel.DENY:
            return ActionLevel.DENY
        rule = self.get_rule(tool_name, tenant_ctx=tenant_ctx)
        if rule is not None:
            if rule.daily_limit is not None and daily_call_count >= rule.daily_limit:
                return ActionLevel.DENY
            if rule.per_goal_limit is not None and goal_call_count >= rule.per_goal_limit:
                return ActionLevel.DENY
        return base

    def list_rules(self, *, tenant_ctx: TenantContext) -> list[PermissionRule]:
        return [
            rule
            for (tid, _), rule in self._rules.items()
            if tid == tenant_ctx.tenant_id
        ]
