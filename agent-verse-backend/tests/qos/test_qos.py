"""Tests for QoSScheduler + PriorityPolicy + BackpressureController — 7 tests."""
from __future__ import annotations

import pytest
from app.qos.priority_policy import PriorityPolicy, QueuePriority
from app.qos.backpressure import BackpressureController
from app.qos.scheduler import QoSScheduler, ScheduledGoal
from app.tenancy.context import TenantContext, PlanTier


def _tenant(plan: PlanTier) -> TenantContext:
    return TenantContext(tenant_id="t1", plan=plan, api_key_id="k1")


# ---------------------------------------------------------------------------
# PriorityPolicy — 3 tests
# ---------------------------------------------------------------------------

def test_priority_enterprise_is_high() -> None:
    policy = PriorityPolicy()
    result = policy.compute(tenant_ctx=_tenant(PlanTier.ENTERPRISE))
    assert result == QueuePriority.HIGH


def test_priority_free_is_low() -> None:
    policy = PriorityPolicy()
    result = policy.compute(tenant_ctx=_tenant(PlanTier.FREE))
    assert result == QueuePriority.LOW


def test_priority_bumps_on_high_risk() -> None:
    policy = PriorityPolicy()
    # FREE normally LOW, bumped to MEDIUM on high risk
    result = policy.compute(tenant_ctx=_tenant(PlanTier.FREE), risk_level="high")
    assert result == QueuePriority.MEDIUM
    # ENTERPRISE normally HIGH, bumped to CRITICAL on high risk
    result2 = policy.compute(tenant_ctx=_tenant(PlanTier.ENTERPRISE), risk_level="high")
    assert result2 == QueuePriority.CRITICAL


# ---------------------------------------------------------------------------
# BackpressureController — 2 tests
# ---------------------------------------------------------------------------

def test_backpressure_not_overloaded_initially() -> None:
    bp = BackpressureController(max_depth=3)
    assert not bp.is_overloaded("t1")
    assert bp.queue_depth("t1") == 0


def test_backpressure_overload_and_recovery() -> None:
    bp = BackpressureController(max_depth=2)
    bp.record_queued("t1")
    bp.record_queued("t1")
    bp.record_queued("t1")
    assert bp.is_overloaded("t1")
    bp.record_completed("t1")
    bp.record_completed("t1")
    assert not bp.is_overloaded("t1")


# ---------------------------------------------------------------------------
# QoSScheduler — 2 tests
# ---------------------------------------------------------------------------

def test_scheduler_enterprise_gets_enterprise_queue() -> None:
    scheduler = QoSScheduler()
    goal = scheduler.schedule("g1", tenant_ctx=_tenant(PlanTier.ENTERPRISE))
    assert goal.queue_name == "goals.enterprise"
    assert goal.goal_id == "g1"


def test_scheduler_free_gets_free_queue() -> None:
    scheduler = QoSScheduler()
    goal = scheduler.schedule("g2", tenant_ctx=_tenant(PlanTier.FREE),
                               priority=QueuePriority.LOW)
    assert goal.queue_name == "goals.free"
    assert goal.priority == QueuePriority.LOW
