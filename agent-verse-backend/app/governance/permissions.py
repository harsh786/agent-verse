"""Permission matrix — per-tenant, per-tool action level configuration.

Action levels (ordered by escalating restriction):
  ALLOW       → execute silently
  ALLOW_LOG   → execute and record in audit trail (default for unconfigured tools)
  APPROVAL    → pause and request human approval via HITL gateway
  DENY        → block immediately, raise GovernanceError
"""

from __future__ import annotations

import enum
import fnmatch
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


# Default-deny posture: tool-name globs that are hard-DENIED for every tenant
# unless that tenant sets an explicit rule allowing them. These are unambiguously
# destructive actions that must never run autonomously without an operator opt-in.
_DEFAULT_DENY_PATTERNS: tuple[str, ...] = (
    "*delete*",
    "*destroy*",
    "*wipe*",
    "*truncate*",
    "*drop_database*",
    "*drop_table*",
    "*purge*",
    "*rm_rf*",
    "*rmrf*",
    "*format_disk*",
    "*factory_reset*",
)


class PermissionMatrix:
    """In-memory per-tenant permission matrix.

    Resolution order for a tool call:
      1. An explicit per-tenant rule (``set_rule``) — always wins.
      2. A tenant-agnostic *default* rule (``set_default_rule``) whose
         ``tool_name`` is matched as a glob against the requested tool name.
      3. The fallback level ``ALLOW_LOG`` (execute but audit).

    This lets the platform ship a safe default-deny posture for destructive
    tools while still allowing a tenant to opt in with an explicit ALLOW rule.
    """

    _DEFAULT = ActionLevel.ALLOW_LOG

    def __init__(self) -> None:
        # Key: (tenant_id, tool_name) → PermissionRule
        self._rules: dict[tuple[str, str], PermissionRule] = {}
        # Tenant-agnostic default rules; tool_name is a glob pattern.
        self._default_rules: list[PermissionRule] = []

    def set_rule(self, rule: PermissionRule, *, tenant_ctx: TenantContext) -> None:
        self._rules[(tenant_ctx.tenant_id, rule.tool_name)] = rule

    def set_default_rule(self, rule: PermissionRule) -> None:
        """Register a tenant-agnostic default rule (``tool_name`` is a glob)."""
        self._default_rules.append(rule)

    def _match_default_rule(self, tool_name: str) -> PermissionRule | None:
        lowered = tool_name.lower()
        for rule in self._default_rules:
            if fnmatch.fnmatch(lowered, rule.tool_name.lower()):
                return rule
        return None

    def get_rule(self, tool_name: str, *, tenant_ctx: TenantContext) -> PermissionRule | None:
        return self._rules.get((tenant_ctx.tenant_id, tool_name))

    def _resolve_rule(self, tool_name: str, *, tenant_ctx: TenantContext) -> PermissionRule | None:
        """Resolve the effective rule: explicit tenant rule, else default glob."""
        rule = self.get_rule(tool_name, tenant_ctx=tenant_ctx)
        if rule is not None:
            return rule
        return self._match_default_rule(tool_name)

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
        rule = self._resolve_rule(tool_name, tenant_ctx=tenant_ctx)
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
        rule = self._resolve_rule(tool_name, tenant_ctx=tenant_ctx)
        if rule is not None:
            if rule.daily_limit is not None and daily_call_count >= rule.daily_limit:
                return ActionLevel.DENY
            if rule.per_goal_limit is not None and goal_call_count >= rule.per_goal_limit:
                return ActionLevel.DENY
        return base

    def list_rules(self, *, tenant_ctx: TenantContext) -> list[PermissionRule]:
        return [rule for (tid, _), rule in self._rules.items() if tid == tenant_ctx.tenant_id]


def build_default_permission_matrix() -> PermissionMatrix:
    """Construct a PermissionMatrix seeded with the platform default-deny posture.

    Destructive tool-name globs (delete/destroy/wipe/truncate/drop/purge/…) are
    DENIED for every tenant unless that tenant registers an explicit ALLOW rule.
    Unconfigured non-destructive tools keep the ALLOW_LOG default.
    """
    matrix = PermissionMatrix()
    for pattern in _DEFAULT_DENY_PATTERNS:
        matrix.set_default_rule(PermissionRule(tool_name=pattern, level=ActionLevel.DENY))
    return matrix
