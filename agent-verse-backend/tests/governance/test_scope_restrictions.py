"""Tests for scope-restricted permissions."""
from __future__ import annotations

import pytest

from app.governance.permissions import ActionLevel, PermissionMatrix, PermissionRule
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="scope-t1", plan=PlanTier.PROFESSIONAL, api_key_id="sk1")


def test_scope_pattern_allows_matching_scope() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="github.push", level=ActionLevel.ALLOW, scope_pattern="acme/*"),
        tenant_ctx=T,
    )
    level = matrix.check("github.push", tenant_ctx=T, scope_value="acme/my-repo")
    assert level == ActionLevel.ALLOW


def test_scope_pattern_denies_non_matching_scope() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="github.push", level=ActionLevel.ALLOW, scope_pattern="acme/*"),
        tenant_ctx=T,
    )
    level = matrix.check("github.push", tenant_ctx=T, scope_value="other-org/repo")
    assert level == ActionLevel.DENY


def test_daily_limit_enforcement() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(
            tool_name="github.create_issue", level=ActionLevel.ALLOW, daily_limit=5
        ),
        tenant_ctx=T,
    )
    # Under limit → allow
    level = matrix.check_with_limits(
        "github.create_issue", tenant_ctx=T, daily_call_count=4
    )
    assert level == ActionLevel.ALLOW
    # At limit → deny
    level2 = matrix.check_with_limits(
        "github.create_issue", tenant_ctx=T, daily_call_count=5
    )
    assert level2 == ActionLevel.DENY


def test_per_goal_limit_enforcement() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(
            tool_name="jira.create_ticket", level=ActionLevel.ALLOW, per_goal_limit=3
        ),
        tenant_ctx=T,
    )
    level = matrix.check_with_limits(
        "jira.create_ticket", tenant_ctx=T, goal_call_count=3
    )
    assert level == ActionLevel.DENY


def test_no_scope_pattern_ignores_scope_value() -> None:
    """If rule has no scope_pattern, any scope_value is allowed."""
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="slack.post", level=ActionLevel.ALLOW), tenant_ctx=T
    )
    level = matrix.check("slack.post", tenant_ctx=T, scope_value="any-channel")
    assert level == ActionLevel.ALLOW


def test_check_with_limits_denies_when_base_level_is_deny() -> None:
    """check_with_limits propagates DENY from the base check."""
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(
            tool_name="github.push",
            level=ActionLevel.ALLOW,
            scope_pattern="acme/*",
            daily_limit=100,
        ),
        tenant_ctx=T,
    )
    # Scope mismatch → base check returns DENY → limits are not consulted
    level = matrix.check_with_limits(
        "github.push",
        tenant_ctx=T,
        scope_value="evil-org/hack",
        daily_call_count=0,
    )
    assert level == ActionLevel.DENY


def test_check_backward_compatible_without_scope_value() -> None:
    """check() with no scope_value still works (backward-compat)."""
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="github.push", level=ActionLevel.ALLOW, scope_pattern="acme/*"),
        tenant_ctx=T,
    )
    # No scope_value → scope_pattern is not evaluated → rule level is returned
    level = matrix.check("github.push", tenant_ctx=T)
    assert level == ActionLevel.ALLOW
