"""Tests for outbound webhook delivery service."""
from __future__ import annotations
import pytest
import respx
import httpx
from app.services.webhook_service import OutboundWebhookService


@pytest.mark.asyncio
async def test_successful_delivery():
    svc = OutboundWebhookService()
    with respx.mock:
        respx.post("https://example.com/hook").mock(return_value=httpx.Response(200))
        delivery = await svc.deliver(
            tenant_id="t1",
            url="https://example.com/hook",
            payload={"event": "goal.complete", "goal_id": "g1"},
        )
    assert delivery.status == "delivered"
    assert delivery.attempts == 1
    assert delivery.delivered_at


@pytest.mark.asyncio
async def test_retry_on_failure():
    svc = OutboundWebhookService()
    call_count = 0

    with respx.mock:
        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(500)
            return httpx.Response(200)

        respx.post("https://example.com/hook").mock(side_effect=side_effect)
        delivery = await svc.deliver(
            tenant_id="t1",
            url="https://example.com/hook",
            payload={"event": "test"},
            max_attempts=3,
        )
    assert delivery.status == "delivered"
    assert delivery.attempts == 3


@pytest.mark.asyncio
async def test_dead_letter_queue():
    svc = OutboundWebhookService()
    with respx.mock:
        respx.post("https://example.com/hook").mock(return_value=httpx.Response(503))
        delivery = await svc.deliver(
            tenant_id="t1",
            url="https://example.com/hook",
            payload={"event": "test"},
            max_attempts=2,
        )
    assert delivery.status == "dead"
    dlq = svc.list_dlq(tenant_id="t1")
    assert any(d.delivery_id == delivery.delivery_id for d in dlq)


def test_tenant_isolation():
    svc = OutboundWebhookService()
    # Manually add delivery for tenant t1
    from app.services.webhook_service import WebhookDelivery
    d = WebhookDelivery(tenant_id="t1", url="http://x.com")
    svc._deliveries[d.delivery_id] = d

    assert svc.get_deliveries(tenant_id="t2") == []
    assert len(svc.get_deliveries(tenant_id="t1")) == 1
