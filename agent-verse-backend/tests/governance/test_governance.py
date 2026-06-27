"""Tests for Phase 6 — Governance & Safety.

Tests cover:
- PermissionMatrix: allow/allow_log/approval/deny rules
- PolicyEngine: evaluate tool calls against policies
- AuditLog: record and query events
- CostController: per-goal and per-tenant budget enforcement
- HITLGateway: approval request lifecycle
"""

from __future__ import annotations

import pytest

from app.governance.permissions import ActionLevel, PermissionMatrix, PermissionRule
from app.governance.policies import Policy, PolicyEngine, PolicyResult
from app.governance.audit import AuditEvent, AuditLog
from app.governance.cost import BudgetConfig, CostController
from app.governance.hitl import ApprovalRequest, ApprovalStatus, HITLGateway
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-a", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_CTX_B = TenantContext(tenant_id="tid-b", plan=PlanTier.STARTER, api_key_id="kid-2")


# ── PermissionMatrix ──────────────────────────────────────────────────────────

def test_permission_allow_returns_allow_level() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="github.create_pr", level=ActionLevel.ALLOW),
        tenant_ctx=_CTX,
    )
    result = matrix.check(tool_name="github.create_pr", tenant_ctx=_CTX)
    assert result == ActionLevel.ALLOW


def test_permission_deny_blocks_tool() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="github.delete_repo", level=ActionLevel.DENY),
        tenant_ctx=_CTX,
    )
    result = matrix.check(tool_name="github.delete_repo", tenant_ctx=_CTX)
    assert result == ActionLevel.DENY


def test_permission_default_is_allow_log() -> None:
    matrix = PermissionMatrix()
    result = matrix.check(tool_name="any_tool_not_configured", tenant_ctx=_CTX)
    assert result == ActionLevel.ALLOW_LOG


def test_permission_tenant_isolation() -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="slack.send_message", level=ActionLevel.DENY),
        tenant_ctx=_CTX,
    )
    # Tenant B has no rules → should get default
    result = matrix.check(tool_name="slack.send_message", tenant_ctx=_CTX_B)
    assert result != ActionLevel.DENY


# ── PolicyEngine ──────────────────────────────────────────────────────────────

def test_policy_blocks_denied_tool() -> None:
    policy = Policy(
        name="no-prod-deletes",
        description="Block all production delete operations",
        denied_tools=["aws.delete_s3_bucket", "github.delete_repo"],
    )
    engine = PolicyEngine(policies=[policy])
    result = engine.evaluate(tool_name="aws.delete_s3_bucket", tenant_ctx=_CTX)
    assert result == PolicyResult.DENY


def test_policy_allows_non_denied_tool() -> None:
    policy = Policy(name="example", description="x", denied_tools=["aws.delete_s3_bucket"])
    engine = PolicyEngine(policies=[policy])
    result = engine.evaluate(tool_name="github.create_pr", tenant_ctx=_CTX)
    assert result == PolicyResult.ALLOW


def test_policy_engine_empty_allows_everything() -> None:
    engine = PolicyEngine(policies=[])
    result = engine.evaluate(tool_name="anything", tenant_ctx=_CTX)
    assert result == PolicyResult.ALLOW


# ── AuditLog ──────────────────────────────────────────────────────────────────

def test_audit_log_records_events() -> None:
    log = AuditLog()
    log.record(
        AuditEvent(
            goal_id="gid-1",
            tool_name="github.create_pr",
            action_level=ActionLevel.ALLOW,
            outcome="success",
        ),
        tenant_ctx=_CTX,
    )
    events = log.query(goal_id="gid-1", tenant_ctx=_CTX)
    assert len(events) == 1
    assert events[0].tool_name == "github.create_pr"


def test_audit_log_tenant_isolation() -> None:
    log = AuditLog()
    log.record(
        AuditEvent(goal_id="gid-1", tool_name="secret_tool", action_level=ActionLevel.ALLOW, outcome="ok"),
        tenant_ctx=_CTX,
    )
    events = log.query(goal_id="gid-1", tenant_ctx=_CTX_B)
    assert len(events) == 0


def test_audit_log_is_append_only() -> None:
    log = AuditLog()
    event = AuditEvent(goal_id="g", tool_name="t", action_level=ActionLevel.ALLOW, outcome="ok")
    log.record(event, tenant_ctx=_CTX)
    events_before = log.query(goal_id="g", tenant_ctx=_CTX)
    count = len(events_before)
    # Cannot delete — log just grows
    log.record(event, tenant_ctx=_CTX)
    assert len(log.query(goal_id="g", tenant_ctx=_CTX)) == count + 1


# ── CostController ────────────────────────────────────────────────────────────

async def test_cost_controller_allows_within_budget() -> None:
    ctrl = CostController(BudgetConfig(per_goal_usd=1.0, per_tenant_daily_usd=100.0))
    allowed = await ctrl.check_and_record(goal_id="gid-1", cost_usd=0.50, tenant_ctx=_CTX)
    assert allowed is True


async def test_cost_controller_blocks_over_goal_budget() -> None:
    ctrl = CostController(BudgetConfig(per_goal_usd=0.10, per_tenant_daily_usd=100.0))
    await ctrl.check_and_record(goal_id="gid-1", cost_usd=0.08, tenant_ctx=_CTX)
    allowed = await ctrl.check_and_record(goal_id="gid-1", cost_usd=0.05, tenant_ctx=_CTX)
    assert allowed is False


async def test_cost_controller_tenant_isolation() -> None:
    ctrl = CostController(BudgetConfig(per_goal_usd=0.10, per_tenant_daily_usd=100.0))
    await ctrl.check_and_record(goal_id="gid-1", cost_usd=0.09, tenant_ctx=_CTX)
    # Same goal_id but different tenant — should be allowed
    allowed = await ctrl.check_and_record(goal_id="gid-1", cost_usd=0.09, tenant_ctx=_CTX_B)
    assert allowed is True


# ── HITLGateway ───────────────────────────────────────────────────────────────

def test_hitl_creates_approval_request() -> None:
    gateway = HITLGateway()
    req_id = gateway.request_approval(
        goal_id="gid-1",
        action="Push to production",
        risk_level="high",
        tenant_ctx=_CTX,
    )
    assert req_id
    req = gateway.get_request(req_id, tenant_ctx=_CTX)
    assert req is not None
    assert req.status == ApprovalStatus.PENDING


def test_hitl_approve_changes_status() -> None:
    gateway = HITLGateway()
    req_id = gateway.request_approval(
        goal_id="gid-1", action="drop table", risk_level="critical", tenant_ctx=_CTX
    )
    gateway.approve(req_id, approver="human@example.com", note="OK this time", tenant_ctx=_CTX)
    req = gateway.get_request(req_id, tenant_ctx=_CTX)
    assert req is not None
    assert req.status == ApprovalStatus.APPROVED


async def test_hitl_reject_changes_status() -> None:
    gateway = HITLGateway()
    req_id = gateway.request_approval(
        goal_id="gid-1", action="delete prod db", risk_level="critical", tenant_ctx=_CTX
    )
    await gateway.reject(req_id, approver="security@example.com", note="Never", tenant_ctx=_CTX)
    req = gateway.get_request(req_id, tenant_ctx=_CTX)
    assert req is not None
    assert req.status == ApprovalStatus.REJECTED


def test_hitl_tenant_isolation() -> None:
    gateway = HITLGateway()
    req_id = gateway.request_approval(
        goal_id="gid-1", action="act", risk_level="low", tenant_ctx=_CTX
    )
    req = gateway.get_request(req_id, tenant_ctx=_CTX_B)
    assert req is None
