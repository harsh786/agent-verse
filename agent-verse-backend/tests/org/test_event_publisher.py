"""Tests for the org audit event publisher — app/org/event_publisher.py"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.org.event_publisher import ORG_AUDIT_EVENTS, OrgEventPublisher


@pytest.mark.asyncio
async def test_publish_returns_correlation_id_when_provided():
    publisher = OrgEventPublisher()
    corr_id = await publisher.publish(
        "org.mission.created", {"mission_id": "m1"}, tenant_id="t1", org_id="org1",
        correlation_id="fixed-id",
    )
    assert corr_id == "fixed-id"


@pytest.mark.asyncio
async def test_publish_generates_correlation_id_when_absent():
    publisher = OrgEventPublisher()
    corr_id = await publisher.publish(
        "org.mission.created", {"mission_id": "m1"}, tenant_id="t1", org_id="org1"
    )
    assert isinstance(corr_id, str) and len(corr_id) > 0


@pytest.mark.asyncio
async def test_publish_warns_on_unknown_event_type_but_still_publishes():
    publisher = OrgEventPublisher()
    corr_id = await publisher.publish(
        "org.totally.unknown", {}, tenant_id="t1", org_id="org1"
    )
    assert corr_id


@pytest.mark.asyncio
async def test_publish_calls_redis_publish():
    redis = AsyncMock()
    publisher = OrgEventPublisher(redis_client=redis)
    await publisher.publish("org.mission.created", {"mission_id": "m1"}, "t1", "org1")
    assert redis.publish.await_count == 1
    channel_arg = redis.publish.await_args.args[0]
    assert channel_arg == "org_events:t1:org1"


@pytest.mark.asyncio
async def test_publish_swallows_redis_errors():
    redis = AsyncMock()
    redis.publish = AsyncMock(side_effect=RuntimeError("redis down"))
    publisher = OrgEventPublisher(redis_client=redis)
    corr_id = await publisher.publish("org.mission.created", {}, "t1", "org1")
    assert corr_id  # did not raise


@pytest.mark.asyncio
async def test_publish_writes_audit_trail():
    audit = AsyncMock()
    publisher = OrgEventPublisher(audit_service=audit)
    await publisher.publish("org.mission.created", {"a": 1}, "t1", "org1", severity="warning")
    assert audit.log.await_count == 1
    kwargs = audit.log.await_args.kwargs
    assert kwargs["event_type"] == "org.mission.created"
    assert kwargs["severity"] == "warning"


@pytest.mark.asyncio
async def test_publish_swallows_audit_errors():
    audit = AsyncMock()
    audit.log = AsyncMock(side_effect=RuntimeError("db down"))
    publisher = OrgEventPublisher(audit_service=audit)
    corr_id = await publisher.publish("org.mission.created", {}, "t1", "org1")
    assert corr_id


# ── convenience wrappers ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_mission_event_builds_correct_type_and_payload():
    publisher = OrgEventPublisher()
    audit = AsyncMock()
    publisher._audit = audit
    await publisher.publish_mission_event(
        "completed", mission_id="m1", org_id="org1", tenant_id="t1", extra={"cost": 3}
    )
    kwargs = audit.log.await_args.kwargs
    assert kwargs["event_type"] == "org.mission.completed"
    assert kwargs["payload"]["mission_id"] == "m1"
    assert kwargs["payload"]["cost"] == 3


@pytest.mark.asyncio
async def test_publish_team_event():
    publisher = OrgEventPublisher()
    audit = AsyncMock()
    publisher._audit = audit
    await publisher.publish_team_event("formed", "team1", "m1", "org1", "t1")
    kwargs = audit.log.await_args.kwargs
    assert kwargs["event_type"] == "org.team.formed"
    assert kwargs["payload"]["team_id"] == "team1"


@pytest.mark.asyncio
async def test_publish_approval_event_severity_warning_for_rejected():
    publisher = OrgEventPublisher()
    audit = AsyncMock()
    publisher._audit = audit
    await publisher.publish_approval_event(
        "rejected", "appr1", "delete_prod_db", "org1", "t1", approver="alice"
    )
    kwargs = audit.log.await_args.kwargs
    assert kwargs["event_type"] == "org.approval.rejected"
    assert kwargs["severity"] == "warning"
    assert kwargs["payload"]["approver"] == "alice"


@pytest.mark.asyncio
async def test_publish_approval_event_severity_info_for_granted():
    publisher = OrgEventPublisher()
    audit = AsyncMock()
    publisher._audit = audit
    await publisher.publish_approval_event("granted", "appr2", "deploy", "org1", "t1")
    kwargs = audit.log.await_args.kwargs
    assert kwargs["severity"] == "info"


@pytest.mark.asyncio
async def test_publish_budget_alert_exceeded_is_critical():
    publisher = OrgEventPublisher()
    audit = AsyncMock()
    publisher._audit = audit
    await publisher.publish_budget_alert(
        1.2, "dept1", "org1", "t1", spent_usd=120.0, budget_usd=100.0
    )
    kwargs = audit.log.await_args.kwargs
    assert kwargs["event_type"] == "org.budget.exceeded"
    assert kwargs["severity"] == "critical"


@pytest.mark.asyncio
async def test_publish_budget_alert_threshold_80_is_warning():
    publisher = OrgEventPublisher()
    audit = AsyncMock()
    publisher._audit = audit
    await publisher.publish_budget_alert(0.8, None, "org1", "t1")
    kwargs = audit.log.await_args.kwargs
    assert kwargs["event_type"] == "org.budget.threshold_80"
    assert kwargs["severity"] == "warning"


@pytest.mark.asyncio
async def test_publish_policy_violation():
    publisher = OrgEventPublisher()
    audit = AsyncMock()
    publisher._audit = audit
    await publisher.publish_policy_violation("unauthorized_tool", "agent1", "delete_db", "org1", "t1")
    kwargs = audit.log.await_args.kwargs
    assert kwargs["event_type"] == "org.policy.violation"
    assert kwargs["severity"] == "warning"
    assert kwargs["payload"]["agent_id"] == "agent1"


@pytest.mark.asyncio
async def test_publish_anomaly():
    publisher = OrgEventPublisher()
    audit = AsyncMock()
    publisher._audit = audit
    await publisher.publish_anomaly("cost_spike", "3x normal spend", "org1", "t1")
    kwargs = audit.log.await_args.kwargs
    assert kwargs["event_type"] == "org.anomaly.detected"
    assert kwargs["severity"] == "critical"
    assert kwargs["payload"]["anomaly_type"] == "cost_spike"


def test_org_audit_events_contains_expected_types():
    assert "org.mission.created" in ORG_AUDIT_EVENTS
    assert "org.digest.ready" in ORG_AUDIT_EVENTS
    assert len(ORG_AUDIT_EVENTS) >= 30
