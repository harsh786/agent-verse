"""Comprehensive tests for app/services/webhook_service.py — targeting 90%+ coverage."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from app.services.webhook_service import (
    OutboundWebhookService,
    WebhookDelivery,
    _DELIVERY_TTL_SECONDS,
    _MAX_DELIVERIES,
)


# ── WebhookDelivery ───────────────────────────────────────────────────────────

class TestWebhookDelivery:
    def test_defaults(self) -> None:
        d = WebhookDelivery()
        assert d.status == "pending"
        assert d.attempts == 0
        assert d.max_attempts == 3
        assert d.last_error == ""
        assert d.delivered_at == ""

    def test_delivery_id_auto_generated(self) -> None:
        d1 = WebhookDelivery()
        d2 = WebhookDelivery()
        assert d1.delivery_id != d2.delivery_id

    def test_custom_fields(self) -> None:
        d = WebhookDelivery(
            tenant_id="t1",
            url="https://example.com/hook",
            payload={"event": "test"},
            max_attempts=5,
        )
        assert d.tenant_id == "t1"
        assert d.url == "https://example.com/hook"
        assert d.max_attempts == 5


# ── OutboundWebhookService ────────────────────────────────────────────────────

class TestOutboundWebhookService:
    async def test_deliver_success_on_first_attempt(self) -> None:
        svc = OutboundWebhookService()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            delivery = await svc.deliver(
                tenant_id="t1",
                url="https://example.com/hook",
                payload={"event": "goal.complete"},
            )

        assert delivery.status == "delivered"
        assert delivery.attempts == 1
        assert delivery.delivered_at != ""

    async def test_deliver_retries_on_failure(self) -> None:
        svc = OutboundWebhookService()
        attempt_count = 0

        async def post_side_effect(url: str, **kwargs: object) -> MagicMock:
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 2:
                raise httpx.NetworkError("Connection refused")
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = AsyncMock()
        mock_client.post = post_side_effect

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.sleep", new_callable=AsyncMock):
                delivery = await svc.deliver(
                    tenant_id="t1",
                    url="https://example.com/hook",
                    payload={"event": "test"},
                )

        assert delivery.status == "delivered"
        assert delivery.attempts == 2

    async def test_deliver_all_attempts_exhausted_dead_letter(self) -> None:
        svc = OutboundWebhookService()

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(
                side_effect=httpx.NetworkError("Connection refused")
            )
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.sleep", new_callable=AsyncMock):
                delivery = await svc.deliver(
                    tenant_id="t1",
                    url="https://example.com/hook",
                    payload={"event": "test"},
                    max_attempts=3,
                )

        assert delivery.status == "dead"
        assert delivery.attempts == 3
        assert delivery.last_error != ""
        # Should be in DLQ
        dlq = svc.list_dlq(tenant_id="t1")
        assert delivery in dlq

    async def test_deliver_with_secret_computes_hmac(self) -> None:
        svc = OutboundWebhookService()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            delivery = await svc.deliver(
                tenant_id="t1",
                url="https://example.com/hook",
                payload={"event": "test"},
                secret="my_secret_key",
            )

        assert delivery.status == "delivered"
        # Verify HMAC header was sent
        call_kwargs = mock_client.post.call_args[1]
        headers = call_kwargs.get("headers", {})
        assert "X-Webhook-Signature" in headers
        assert headers["X-Webhook-Signature"].startswith("sha256=")

    async def test_get_delivery_returns_record(self) -> None:
        svc = OutboundWebhookService()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            delivery = await svc.deliver(
                tenant_id="t1", url="https://example.com", payload={}
            )

        found = svc.get_delivery(delivery.delivery_id)
        assert found is delivery

    def test_get_delivery_unknown_returns_none(self) -> None:
        svc = OutboundWebhookService()
        assert svc.get_delivery("nonexistent") is None

    def test_list_dlq_filters_by_tenant(self) -> None:
        svc = OutboundWebhookService()
        d1 = WebhookDelivery(tenant_id="t1", status="dead")
        d2 = WebhookDelivery(tenant_id="t2", status="dead")
        svc._dlq.extend([d1, d2])
        assert svc.list_dlq(tenant_id="t1") == [d1]
        assert svc.list_dlq(tenant_id="t2") == [d2]

    def test_get_deliveries_filters_by_tenant(self) -> None:
        svc = OutboundWebhookService()
        d1 = WebhookDelivery(tenant_id="t1")
        d2 = WebhookDelivery(tenant_id="t2")
        svc._deliveries[d1.delivery_id] = d1
        svc._deliveries[d2.delivery_id] = d2
        result = svc.get_deliveries(tenant_id="t1")
        assert result == [d1]


# ── _evict_old_deliveries ─────────────────────────────────────────────────────

class TestEvictOldDeliveries:
    def test_removes_old_deliveries(self) -> None:
        svc = OutboundWebhookService()
        old_time = (datetime.now(UTC) - timedelta(seconds=_DELIVERY_TTL_SECONDS + 60)).isoformat()
        d = WebhookDelivery(tenant_id="t1")
        d.created_at = old_time
        svc._deliveries[d.delivery_id] = d
        removed = svc._evict_old_deliveries()
        assert removed == 1
        assert d.delivery_id not in svc._deliveries

    def test_keeps_recent_deliveries(self) -> None:
        svc = OutboundWebhookService()
        d = WebhookDelivery(tenant_id="t1")
        svc._deliveries[d.delivery_id] = d
        removed = svc._evict_old_deliveries()
        assert removed == 0
        assert d.delivery_id in svc._deliveries

    def test_caps_deliveries_at_max(self) -> None:
        svc = OutboundWebhookService()
        # Add more than _MAX_DELIVERIES
        for i in range(_MAX_DELIVERIES + 10):
            d = WebhookDelivery(tenant_id="t1")
            svc._deliveries[d.delivery_id] = d
        svc._evict_old_deliveries()
        assert len(svc._deliveries) <= _MAX_DELIVERIES

    def test_caps_dlq_at_1000(self) -> None:
        svc = OutboundWebhookService()
        for _ in range(1100):
            svc._dlq.append(WebhookDelivery(tenant_id="t1", status="dead"))
        svc._evict_old_deliveries()
        assert len(svc._dlq) == 1000

    def test_handles_invalid_created_at(self) -> None:
        svc = OutboundWebhookService()
        d = WebhookDelivery(tenant_id="t1")
        d.created_at = "not-a-valid-date"
        svc._deliveries[d.delivery_id] = d
        # Should not raise
        svc._evict_old_deliveries()

    def test_evict_triggered_every_100_deliveries(self) -> None:
        """Eviction is called every 100th deliver() call."""
        svc = OutboundWebhookService()
        svc._deliver_count = 99  # next deliver() will be 100th

        mock_evict = MagicMock(return_value=0)
        svc._evict_old_deliveries = mock_evict

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        async def run_deliver() -> None:
            with patch("httpx.AsyncClient") as mock_httpx:
                mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
                await svc.deliver(tenant_id="t1", url="https://example.com", payload={})

        asyncio.run(run_deliver())
        mock_evict.assert_called_once()


# ── HMAC signature ────────────────────────────────────────────────────────────

class TestHMACSignature:
    async def test_hmac_signature_deterministic(self) -> None:
        """Same payload + secret → same signature."""
        svc = OutboundWebhookService()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        signatures = []

        async def capture_post(url: str, **kwargs: object) -> MagicMock:
            headers = kwargs.get("headers", {})
            signatures.append(headers.get("X-Webhook-Signature", ""))
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = capture_post

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            for _ in range(2):
                await svc.deliver(
                    tenant_id="t1",
                    url="https://example.com",
                    payload={"key": "value"},
                    secret="test_secret",
                )

        assert signatures[0] == signatures[1]
        assert signatures[0].startswith("sha256=")
