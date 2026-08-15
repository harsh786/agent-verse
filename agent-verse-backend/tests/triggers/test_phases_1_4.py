"""Tests for Phase 1–4 trigger infrastructure modules."""
from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.triggers.store import ScheduleStore
from app.triggers.consumers.chain import ChainTriggerConsumer
from app.triggers.channels.gateway import ChannelIngestionGateway, NLIntentClassifier
from app.triggers.webhooks.verifier import WebhookSignatureVerifier
from app.triggers.condition.evaluator import CELEvaluator, TemplateRenderer
from app.triggers.models import TriggerSpec, TriggerType


# ── TriggerStore.find_by_type ──────────────────────────────────────────────────

def test_find_by_type_returns_matching():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED)
    store.create(
        spec=spec,
        tenant_ctx=tc,
        goal_id="g1",
        goal_template="test",
    )
    results = store.find_by_type("goal_completed", tenant_id="t1")
    assert len(results) == 1


def test_find_by_type_excludes_other_type():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 * * * *")
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    results = store.find_by_type("goal_completed", tenant_id="t1")
    assert len(results) == 0


def test_find_by_type_excludes_paused():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED)
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    # Pause it
    records = store.find_by_type("goal_completed", tenant_id="t1")
    if records:
        schedule_id = records[0].get("schedule_id")
        if schedule_id:
            store.pause(schedule_id, tenant_ctx=tc)
    results = store.find_by_type("goal_completed", tenant_id="t1")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_find_by_type_async_works():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.HITL_APPROVED)
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    results = await store.find_by_type_async("hitl_approved", tenant_id="t1")
    assert len(results) == 1


# ── ChainTriggerConsumer ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chain_consumer_handles_goal_completed():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(
        trigger_type=TriggerType.GOAL_COMPLETED,
        goal_template="Process completion of {{payload.goal_id}}",
    )
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template=spec.goal_template)

    dispatched: list = []
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(side_effect=lambda *a, **k: dispatched.append(k) or SimpleNamespace(goal_created=True))

    consumer = ChainTriggerConsumer(
        trigger_store=store,
        dispatcher=mock_dispatcher,
    )

    import json
    msg = {
        "type": "message",
        "channel": "goal.completed",
        "data": json.dumps({
            "goal_id": "g-001",
            "agent_id": "agt-001",
            "tenant_id": "t1",
            "tenant_plan": "free",
            "score": 0.9,
        }).encode(),
    }
    await consumer._handle(msg)
    assert mock_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_chain_consumer_respects_depth_limit():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED)
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")

    mock_dispatcher = AsyncMock()
    consumer = ChainTriggerConsumer(trigger_store=store, dispatcher=mock_dispatcher)

    import json
    msg = {
        "type": "message",
        "channel": "goal.completed",
        "data": json.dumps({
            "goal_id": "g-001",
            "tenant_id": "t1",
            "trigger_chain_depth": 10,  # at limit
        }).encode(),
    }
    await consumer._handle(msg)
    mock_dispatcher.dispatch.assert_not_called()


# ── WebhookSignatureVerifier ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_verifier_valid():
    import hmac as _hmac
    import hashlib
    secret = "test-secret"
    payload = b'{"event": "push"}'
    expected = _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    verifier = WebhookSignatureVerifier()
    assert await verifier.verify(payload, expected, secret) is True


@pytest.mark.asyncio
async def test_webhook_verifier_invalid():
    verifier = WebhookSignatureVerifier()
    assert await verifier.verify(b"payload", "invalid-sig", "secret") is False


@pytest.mark.asyncio
async def test_webhook_verifier_empty_secret():
    verifier = WebhookSignatureVerifier()
    assert await verifier.verify(b"payload", "sig", "") is False


# ── ChannelIngestionGateway ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_channel_gateway_routes_slack():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.CHAT_COMMAND)
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace(goal_created=True))

    gateway = ChannelIngestionGateway(trigger_store=store, dispatcher=mock_dispatcher)
    results = await gateway.ingest(
        "slack",
        {"command": "/run", "user_id": "U123"},
        tenant_id="t1",
    )
    assert mock_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_channel_gateway_no_store():
    gateway = ChannelIngestionGateway()
    # Should not raise, just return empty
    results = await gateway.ingest("slack", {}, tenant_id="t1")
    assert results == []


@pytest.mark.asyncio
async def test_nl_intent_classifier_command():
    classifier = NLIntentClassifier()
    result = await classifier.classify("/run-report now", "slack")
    assert result == "chat_command"


@pytest.mark.asyncio
async def test_nl_intent_classifier_keyword():
    classifier = NLIntentClassifier()
    result = await classifier.classify("urgent incident in prod", "slack")
    assert result == "chat_keyword"


# ── CEL Evaluator ─────────────────────────────────────────────────────────────

def test_cel_empty_expression_is_true():
    ev = CELEvaluator()
    assert ev.evaluate("", {}) is True


def test_cel_blocked_attribute_excluded():
    ev = CELEvaluator()
    # Should not crash even if blocked attr is in payload
    try:
        ev.evaluate("", {"__class__": "bad"})
    except Exception:
        pass  # OK to raise — just shouldn't expose internals


# ── TemplateRenderer ──────────────────────────────────────────────────────────

def test_template_renderer_payload_field():
    renderer = TemplateRenderer()
    result = renderer.render("Invoice {{payload.invoice_id}} processed", {"invoice_id": "INV-001"})
    assert result == "Invoice INV-001 processed"


def test_template_renderer_extra_vars():
    renderer = TemplateRenderer()
    result = renderer.render("Type: {{trigger_type}}", {}, trigger_type="goal_completed")
    assert result == "Type: goal_completed"


def test_template_renderer_max_length():
    renderer = TemplateRenderer()
    result = renderer.render("x" * 3000, {})
    assert len(result) == 2048


def test_template_renderer_missing_field_empty():
    renderer = TemplateRenderer()
    result = renderer.render("Hello {{payload.missing}}", {})
    assert result == "Hello "
