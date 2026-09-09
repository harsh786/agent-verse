from __future__ import annotations

import pytest

from app.lifecycle.deletion_orchestrator import DeletionOrchestrator
from app.lifecycle.export_policy import ExportPolicy
from app.lifecycle.legal_hold_policy import LegalHoldPolicy
from app.lifecycle.retention_policy import DataCategory, RetentionPolicy, RetentionTier


def test_retention_default_for_goals():
    policy = RetentionPolicy()
    tier = policy.get_tier(DataCategory.GOAL_ARTIFACT)
    assert tier in (RetentionTier.DEFAULT, RetentionTier.SHORT, RetentionTier.REGULATED)


def test_retention_regulated_for_pii():
    policy = RetentionPolicy()
    assert policy.get_tier(DataCategory.PII_DATA) == RetentionTier.REGULATED


def test_retention_long_for_audit():
    policy = RetentionPolicy()
    assert policy.get_tier(DataCategory.AUDIT_LOG) in (RetentionTier.LONG, RetentionTier.LEGAL_HOLD)


def test_deletion_orchestrator():
    orch = DeletionOrchestrator()
    result = orch.schedule_deletion("t1", DataCategory.GOAL_ARTIFACT, ["g1", "g2"])
    assert result.scheduled_count == 2 and result.tenant_id == "t1"


def test_legal_hold_blocks_deletion():
    policy = LegalHoldPolicy()
    policy.place_hold(tenant_id="t1", record_id="g1", reason="litigation")
    assert policy.has_hold(tenant_id="t1", record_id="g1") is True


def test_legal_hold_release():
    policy = LegalHoldPolicy()
    policy.place_hold(tenant_id="t1", record_id="g2", reason="audit")
    policy.release_hold(tenant_id="t1", record_id="g2")
    assert policy.has_hold(tenant_id="t1", record_id="g2") is False


def test_export_allows_tenant():
    policy = ExportPolicy()
    assert policy.can_export("t1", "admin", DataCategory.GOAL_ARTIFACT) is True


def test_export_blocks_cross_tenant():
    policy = ExportPolicy()
    assert (
        policy.can_export("t1", "admin", DataCategory.GOAL_ARTIFACT, requesting_tenant_id="t2")
        is False
    )
