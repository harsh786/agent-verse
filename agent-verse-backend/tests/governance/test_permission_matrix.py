"""Systematic coverage of app/governance/permissions.py::PermissionMatrix.

PermissionMatrix resolves an ActionLevel (ALLOW / ALLOW_LOG / APPROVAL / DENY)
for a (tenant, tool_name, scope_value) triple, with three layers of
resolution — explicit per-tenant rule, tenant-agnostic default glob rule,
fallback ALLOW_LOG — plus scope-pattern and rate-limit gating. Prior coverage
was incidental (via pipeline/agent-loop tests that inject a matrix as a
dependency); nothing exercised the matrix's own resolution rules directly or
systematically. This file does.
"""

from __future__ import annotations

import pytest

from app.governance.permissions import (
    ActionLevel,
    PermissionMatrix,
    PermissionRule,
    build_default_permission_matrix,
)
from app.tenancy.context import PlanTier, TenantContext

_CTX_A = TenantContext(tenant_id="tenant-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka")
_CTX_B = TenantContext(tenant_id="tenant-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb")

ALL_LEVELS = [ActionLevel.ALLOW, ActionLevel.ALLOW_LOG, ActionLevel.APPROVAL, ActionLevel.DENY]


# ── fallback / unconfigured behaviour ───────────────────────────────────────────


def test_unconfigured_tool_defaults_to_allow_log() -> None:
    matrix = PermissionMatrix()
    assert matrix.check("anything.unconfigured", tenant_ctx=_CTX_A) == ActionLevel.ALLOW_LOG


def test_unconfigured_tool_is_allow_log_for_every_tenant_independently() -> None:
    matrix = PermissionMatrix()
    for ctx in (_CTX_A, _CTX_B):
        assert matrix.check("no_rule_tool", tenant_ctx=ctx) == ActionLevel.ALLOW_LOG


# ── explicit rule resolution: every ActionLevel round-trips correctly ──────────


@pytest.mark.parametrize("level", ALL_LEVELS)
def test_explicit_rule_is_returned_verbatim_for_each_action_level(level: ActionLevel) -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="my_tool", level=level), tenant_ctx=_CTX_A
    )
    assert matrix.check("my_tool", tenant_ctx=_CTX_A) == level


# ── resolution order: explicit tenant rule beats default glob rule ─────────────


def test_explicit_tenant_rule_overrides_default_deny_glob() -> None:
    """A tenant can opt a specific destructive tool back into ALLOW even
    though it matches a platform-wide default-deny glob."""
    matrix = build_default_permission_matrix()
    # Unconfigured: matches "*delete*" default → DENY.
    assert matrix.check("delete_record", tenant_ctx=_CTX_A) == ActionLevel.DENY

    matrix.set_rule(
        PermissionRule(tool_name="delete_record", level=ActionLevel.ALLOW), tenant_ctx=_CTX_A
    )
    assert matrix.check("delete_record", tenant_ctx=_CTX_A) == ActionLevel.ALLOW

    # The opt-in is per-tenant: tenant B is still denied.
    assert matrix.check("delete_record", tenant_ctx=_CTX_B) == ActionLevel.DENY


def test_default_glob_rule_applies_when_no_explicit_rule_exists() -> None:
    matrix = PermissionMatrix()
    matrix.set_default_rule(PermissionRule(tool_name="report_*", level=ActionLevel.APPROVAL))

    assert matrix.check("report_quarterly", tenant_ctx=_CTX_A) == ActionLevel.APPROVAL
    # Non-matching tool falls through to the global fallback.
    assert matrix.check("unrelated_tool", tenant_ctx=_CTX_A) == ActionLevel.ALLOW_LOG


def test_default_glob_matching_is_case_insensitive() -> None:
    matrix = PermissionMatrix()
    matrix.set_default_rule(PermissionRule(tool_name="*delete*", level=ActionLevel.DENY))
    assert matrix.check("DELETE_USER", tenant_ctx=_CTX_A) == ActionLevel.DENY
    assert matrix.check("Delete_User", tenant_ctx=_CTX_A) == ActionLevel.DENY


def test_first_matching_default_rule_wins_when_multiple_match() -> None:
    """Default rules are evaluated in registration order; the first glob match
    is authoritative, so registration order is itself part of the contract."""
    matrix = PermissionMatrix()
    matrix.set_default_rule(PermissionRule(tool_name="*_admin_*", level=ActionLevel.APPROVAL))
    matrix.set_default_rule(PermissionRule(tool_name="*delete*", level=ActionLevel.DENY))

    # Matches both globs — the first-registered rule (APPROVAL) wins.
    assert matrix.check("delete_admin_user", tenant_ctx=_CTX_A) == ActionLevel.APPROVAL


# ── every default-deny pattern is enforced platform-wide ────────────────────────


_DEFAULT_DENY_SAMPLE_TOOLS = [
    "delete_file",
    "destroy_instance",
    "wipe_disk",
    "truncate_table",
    "drop_database_now",
    "drop_table_users",
    "purge_cache",
    "rm_rf_tmp",
    "rmrf_everything",
    "format_disk_c",
    "factory_reset_device",
]


@pytest.mark.parametrize("tool_name", _DEFAULT_DENY_SAMPLE_TOOLS)
def test_default_deny_pattern_denies_matching_tool(tool_name: str) -> None:
    matrix = build_default_permission_matrix()
    assert matrix.check(tool_name, tenant_ctx=_CTX_A) == ActionLevel.DENY


def test_default_deny_matrix_does_not_deny_unrelated_tools() -> None:
    matrix = build_default_permission_matrix()
    assert matrix.check("send_email", tenant_ctx=_CTX_A) == ActionLevel.ALLOW_LOG
    assert matrix.check("read_document", tenant_ctx=_CTX_A) == ActionLevel.ALLOW_LOG


# ── scope_pattern gating ─────────────────────────────────────────────────────────


def test_scope_mismatch_forces_deny_even_for_an_allow_rule() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(
            tool_name="deploy", level=ActionLevel.ALLOW, scope_pattern="prod-*"
        ),
        tenant_ctx=_CTX_A,
    )
    assert matrix.check("deploy", tenant_ctx=_CTX_A, scope_value="staging-1") == ActionLevel.DENY
    assert matrix.check("deploy", tenant_ctx=_CTX_A, scope_value="prod-1") == ActionLevel.ALLOW


def test_scope_pattern_not_evaluated_when_scope_value_omitted() -> None:
    """No scope_value supplied means scope gating is skipped entirely — the
    rule's own level is returned, not a forced DENY."""
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(
            tool_name="deploy", level=ActionLevel.ALLOW, scope_pattern="prod-*"
        ),
        tenant_ctx=_CTX_A,
    )
    assert matrix.check("deploy", tenant_ctx=_CTX_A) == ActionLevel.ALLOW


def test_scope_pattern_on_deny_rule_stays_deny_regardless_of_match() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(
            tool_name="wipe", level=ActionLevel.DENY, scope_pattern="prod-*"
        ),
        tenant_ctx=_CTX_A,
    )
    assert matrix.check("wipe", tenant_ctx=_CTX_A, scope_value="prod-1") == ActionLevel.DENY
    assert matrix.check("wipe", tenant_ctx=_CTX_A, scope_value="dev-1") == ActionLevel.DENY


# ── rate limits: check_with_limits ──────────────────────────────────────────────


def test_daily_limit_denies_at_and_above_the_limit() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="send_sms", level=ActionLevel.ALLOW, daily_limit=3),
        tenant_ctx=_CTX_A,
    )
    assert matrix.check_with_limits(
        "send_sms", tenant_ctx=_CTX_A, daily_call_count=2
    ) == ActionLevel.ALLOW
    assert matrix.check_with_limits(
        "send_sms", tenant_ctx=_CTX_A, daily_call_count=3
    ) == ActionLevel.DENY
    assert matrix.check_with_limits(
        "send_sms", tenant_ctx=_CTX_A, daily_call_count=4
    ) == ActionLevel.DENY


def test_per_goal_limit_denies_at_and_above_the_limit() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="web_search", level=ActionLevel.ALLOW_LOG, per_goal_limit=5),
        tenant_ctx=_CTX_A,
    )
    assert matrix.check_with_limits(
        "web_search", tenant_ctx=_CTX_A, goal_call_count=4
    ) == ActionLevel.ALLOW_LOG
    assert matrix.check_with_limits(
        "web_search", tenant_ctx=_CTX_A, goal_call_count=5
    ) == ActionLevel.DENY


def test_base_deny_short_circuits_before_limits_are_checked() -> None:
    """A DENY from the base check (e.g. scope mismatch) must win even if the
    call counts are still well within any configured limits."""
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(
            tool_name="deploy",
            level=ActionLevel.ALLOW,
            scope_pattern="prod-*",
            daily_limit=100,
        ),
        tenant_ctx=_CTX_A,
    )
    result = matrix.check_with_limits(
        "deploy", tenant_ctx=_CTX_A, scope_value="staging-1", daily_call_count=0
    )
    assert result == ActionLevel.DENY


def test_check_with_limits_matches_check_when_no_limits_configured() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(PermissionRule(tool_name="read_kb", level=ActionLevel.APPROVAL), tenant_ctx=_CTX_A)
    assert matrix.check_with_limits("read_kb", tenant_ctx=_CTX_A) == ActionLevel.APPROVAL


def test_limits_do_not_apply_when_no_rule_is_configured() -> None:
    """An unconfigured tool has no daily/per_goal limit to violate — limits
    are only ever enforced alongside an explicit or default rule."""
    matrix = PermissionMatrix()
    result = matrix.check_with_limits(
        "unconfigured_tool", tenant_ctx=_CTX_A, daily_call_count=999999
    )
    assert result == ActionLevel.ALLOW_LOG


# ── tenant isolation of the matrix itself ───────────────────────────────────────


def test_rules_are_isolated_per_tenant() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(PermissionRule(tool_name="deploy", level=ActionLevel.DENY), tenant_ctx=_CTX_A)

    # Tenant B has no rule for "deploy" — must NOT inherit tenant A's DENY.
    assert matrix.check("deploy", tenant_ctx=_CTX_B) == ActionLevel.ALLOW_LOG
    assert matrix.check("deploy", tenant_ctx=_CTX_A) == ActionLevel.DENY


def test_list_rules_only_returns_the_calling_tenants_rules() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(PermissionRule(tool_name="tool_a1", level=ActionLevel.ALLOW), tenant_ctx=_CTX_A)
    matrix.set_rule(PermissionRule(tool_name="tool_a2", level=ActionLevel.DENY), tenant_ctx=_CTX_A)
    matrix.set_rule(PermissionRule(tool_name="tool_b1", level=ActionLevel.APPROVAL), tenant_ctx=_CTX_B)

    rules_a = {r.tool_name for r in matrix.list_rules(tenant_ctx=_CTX_A)}
    rules_b = {r.tool_name for r in matrix.list_rules(tenant_ctx=_CTX_B)}

    assert rules_a == {"tool_a1", "tool_a2"}
    assert rules_b == {"tool_b1"}
    assert rules_a.isdisjoint(rules_b)


def test_default_rules_are_shared_across_all_tenants() -> None:
    """Unlike explicit rules, default glob rules are intentionally global —
    every tenant sees the same platform-wide defaults."""
    matrix = PermissionMatrix()
    matrix.set_default_rule(PermissionRule(tool_name="risky_*", level=ActionLevel.APPROVAL))

    for ctx in (_CTX_A, _CTX_B):
        assert matrix.check("risky_operation", tenant_ctx=ctx) == ActionLevel.APPROVAL


# ── get_rule ──────────────────────────────────────────────────────────────────


def test_get_rule_returns_none_when_unconfigured() -> None:
    matrix = PermissionMatrix()
    assert matrix.get_rule("nope", tenant_ctx=_CTX_A) is None


def test_get_rule_does_not_fall_back_to_default_rules() -> None:
    """get_rule() is the raw explicit-rule accessor — it must not silently
    surface a default glob match, only an exact per-tenant rule."""
    matrix = PermissionMatrix()
    matrix.set_default_rule(PermissionRule(tool_name="*", level=ActionLevel.DENY))
    assert matrix.get_rule("anything", tenant_ctx=_CTX_A) is None
