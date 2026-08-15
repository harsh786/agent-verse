"""Unit tests for Phase 0 trigger infrastructure modules."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.dedup import derive_idempotency_key
from app.triggers.rate_limiter import TriggerRateLimiter, effective_rate_cap
from app.triggers.circuit_breaker import TriggerCircuitBreaker, CircuitBreakerRegistry
from app.triggers.bulkhead import TriggerBulkhead, PLAN_CONCURRENCY
from app.triggers.quota import TriggerQuotaEnforcer, TriggerQuotaExceeded
from app.triggers.rbac import check_permission, TriggerPermissionDenied
from app.triggers.events import TriggerEvent, SimulatedTriggerResult
from app.triggers.simulation import get_sample_payload, TriggerChaosHarness
from app.triggers.dispatcher import TriggerDispatcher


# ── TriggerType ───────────────────────────────────────────────────────────────

def test_trigger_type_count():
    assert len(TriggerType) == 58


def test_all_9_families_present():
    values = [t.value for t in TriggerType]
    assert "cron" in values            # A
    assert "goal_completed" in values  # B
    assert "chat_command" in values    # C
    assert "condition" in values       # D
    assert "webhook" in values         # E
    assert "db_row_change" in values   # F
    assert "pagerduty" in values       # G
    assert "api_poll" in values        # H
    assert "mqtt" in values            # I


# ── TriggerSpec ───────────────────────────────────────────────────────────────

def test_trigger_spec_has_all_field_families():
    spec = TriggerSpec(trigger_type=TriggerType.MQTT)
    assert hasattr(spec, "mqtt_topic")
    assert hasattr(spec, "cron_expression")
    assert hasattr(spec, "goal_template")
    assert hasattr(spec, "condition")
    assert hasattr(spec, "simulation_mode")
    assert hasattr(spec, "allowed_roles")
    assert hasattr(spec, "price_threshold")
    assert hasattr(spec, "geofence_polygon")


# ── Deduplication ─────────────────────────────────────────────────────────────

def test_dedup_key_deterministic():
    k1 = derive_idempotency_key("t1", "cron", {}, scheduled_fire_time="2026-01-01T09:00:00Z")
    k2 = derive_idempotency_key("t1", "cron", {}, scheduled_fire_time="2026-01-01T09:00:00Z")
    assert k1 == k2


def test_dedup_key_differs_for_different_triggers():
    k1 = derive_idempotency_key("t1", "webhook", {"event": "push"})
    k2 = derive_idempotency_key("t2", "webhook", {"event": "push"})
    assert k1 != k2


def test_dedup_key_differs_for_different_times():
    k1 = derive_idempotency_key("t1", "cron", {}, scheduled_fire_time="T1")
    k2 = derive_idempotency_key("t1", "cron", {}, scheduled_fire_time="T2")
    assert k1 != k2


def test_dedup_key_family_b_uses_goal_id():
    k1 = derive_idempotency_key("t1", "goal_completed", {}, source_goal_id="g1",
                                 completion_event_id="e1")
    k2 = derive_idempotency_key("t1", "goal_completed", {}, source_goal_id="g2",
                                 completion_event_id="e1")
    assert k1 != k2


# ── Rate Limiter ──────────────────────────────────────────────────────────────

def test_effective_rate_cap_respects_plan():
    assert effective_rate_cap(0, "free") == 10
    assert effective_rate_cap(0, "starter") == 60
    assert effective_rate_cap(5, "starter") == 5      # user cap < plan cap
    assert effective_rate_cap(100, "free") == 10      # plan cap overrides


@pytest.mark.asyncio
async def test_rate_limiter_no_redis_allows():
    limiter = TriggerRateLimiter(redis=None)
    assert await limiter.check("t1", 5, "free") is True


@pytest.mark.asyncio
async def test_rate_limiter_blocks_when_exceeded():
    redis_mock = AsyncMock()
    redis_mock.incr = AsyncMock(return_value=11)
    redis_mock.expire = AsyncMock()
    limiter = TriggerRateLimiter(redis=redis_mock)
    result = await limiter.check("t1", 10, "free")
    assert result is False


# ── Circuit Breaker ───────────────────────────────────────────────────────────

def test_circuit_breaker_opens_after_5_failures():
    cb = TriggerCircuitBreaker(trigger_id="t1")
    for _ in range(5):
        cb.record_failure()
    assert cb.state == "open"


def test_circuit_breaker_closed_allows():
    cb = TriggerCircuitBreaker(trigger_id="t1")
    assert cb.is_open() is False


def test_circuit_breaker_open_blocks():
    cb = TriggerCircuitBreaker(trigger_id="t1", state="open",
                               failure_count=5, last_failure_at=9e9)
    assert cb.is_open() is True


def test_circuit_breaker_half_open_to_closed():
    cb = TriggerCircuitBreaker(trigger_id="t1", state="half_open",
                               success_threshold=2)
    cb.record_success()
    assert cb.state == "half_open"
    cb.record_success()
    assert cb.state == "closed"


def test_circuit_breaker_registry():
    registry = CircuitBreakerRegistry()
    cb1 = registry.get("t1")
    cb2 = registry.get("t1")
    assert cb1 is cb2


# ── Bulkhead ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulkhead_no_redis_allows():
    bh = TriggerBulkhead(redis=None)
    assert await bh.acquire("tenant1", "free") is True


@pytest.mark.asyncio
async def test_bulkhead_blocks_when_full():
    redis_mock = AsyncMock()
    redis_mock.incr = AsyncMock(return_value=3)  # over free cap of 2
    redis_mock.decr = AsyncMock(return_value=2)
    redis_mock.expire = AsyncMock()
    bh = TriggerBulkhead(redis=redis_mock)
    assert await bh.acquire("tenant1", "free") is False


# ── Quota ─────────────────────────────────────────────────────────────────────

def test_quota_allows_under_limit():
    q = TriggerQuotaEnforcer()
    q.check_create(4, "free")  # limit is 5 — should not raise


def test_quota_blocks_at_limit():
    q = TriggerQuotaEnforcer()
    with pytest.raises(TriggerQuotaExceeded):
        q.check_create(5, "free")  # limit is 5


def test_quota_enterprise_very_high():
    q = TriggerQuotaEnforcer()
    q.check_create(999998, "enterprise")  # should not raise


# ── RBAC ─────────────────────────────────────────────────────────────────────

def test_rbac_admin_can_do_everything():
    for op in ["create", "read", "update", "delete", "fire", "pause", "resume", "view_dlq"]:
        check_permission("admin", op)  # should not raise


def test_rbac_viewer_limited():
    check_permission("viewer", "read")
    with pytest.raises(TriggerPermissionDenied):
        check_permission("viewer", "create")


def test_rbac_operator_can_fire():
    check_permission("operator", "fire")
    with pytest.raises(TriggerPermissionDenied):
        check_permission("operator", "delete")


# ── Simulation ────────────────────────────────────────────────────────────────

def test_test_payload_factory_github():
    p = get_sample_payload("github_webhook")
    assert "repository" in p


def test_test_payload_factory_mqtt():
    p = get_sample_payload("mqtt")
    assert "device_id" in p


def test_test_payload_factory_unknown_returns_default():
    p = get_sample_payload("unknown_type_xyz")
    assert "trigger_type" in p


def test_chaos_harness_context_manager():
    harness = TriggerChaosHarness(inject_signature_failure_pct=100)
    with harness:
        assert harness.should_fail_signature() is True
    assert harness._active is False


# ── Dispatcher ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_happy_path():
    """Dispatcher should create a goal on successful dispatch."""
    goal_service = AsyncMock()
    goal_service.create_goal = AsyncMock(return_value=SimpleNamespace(goal_id="g-001"))

    dispatcher = TriggerDispatcher(goal_service=goal_service)
    spec = TriggerSpec(
        trigger_type=TriggerType.REST,
        goal_template="Test goal",
    )
    spec.trigger_id = "trg-001"  # type: ignore[attr-defined]
    tenant_ctx = SimpleNamespace(tenant_id="t-001", plan="starter")

    result = await dispatcher.dispatch(spec, {"event": "test"}, tenant_ctx)
    assert result.goal_created is True


@pytest.mark.asyncio
async def test_dispatch_simulation_mode_no_goal():
    dispatcher = TriggerDispatcher()
    spec = TriggerSpec(trigger_type=TriggerType.REST, goal_template="Test")
    spec.trigger_id = "trg-002"  # type: ignore[attr-defined]
    tenant_ctx = SimpleNamespace(tenant_id="t-001", plan="free")

    result = await dispatcher.dispatch(spec, {}, tenant_ctx, simulation=True)
    assert isinstance(result, SimulatedTriggerResult)
    assert result.would_have_fired is True


@pytest.mark.asyncio
async def test_dispatch_rbac_denied_for_viewer():
    dispatcher = TriggerDispatcher()
    spec = TriggerSpec(trigger_type=TriggerType.REST)
    spec.trigger_id = "trg-003"  # type: ignore[attr-defined]
    tenant_ctx = SimpleNamespace(tenant_id="t-001", plan="free")

    result = await dispatcher.dispatch(spec, {}, tenant_ctx,
                                        caller_role="viewer")
    assert result.skip_reason == "RBAC_DENIED"


@pytest.mark.asyncio
async def test_dispatch_condition_false_skips():
    dispatcher = TriggerDispatcher()
    spec = TriggerSpec(
        trigger_type=TriggerType.CONDITION,
        condition="this_is_definitely_invalid_expression_xyz",
        goal_template="Test",
    )
    spec.trigger_id = "trg-004"  # type: ignore[attr-defined]
    tenant_ctx = SimpleNamespace(tenant_id="t-001", plan="free")

    # condition evaluation fails → skip with condition_false
    with patch.object(dispatcher, "_evaluate_condition", return_value=False):
        result = await dispatcher.dispatch(spec, {}, tenant_ctx)
    assert result.skip_reason == "condition_false"
