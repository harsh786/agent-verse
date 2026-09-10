"""Phase 2 — Webhook delivery tests.

Covers app/gateway/webhook_delivery.py:
  - WebhookDelivery.sign_payload()  — HMAC-SHA256 signing
  - WebhookDelivery.is_dead()       — death threshold detection
  - WebhookDeliverySystem (in-memory, no real HTTP)
    - register_webhook / deliver / list_dlq / get_stats
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.gateway.webhook_delivery import (
    BACKOFF_SCHEDULE,
    MAX_RETRIES,
    WebhookConfig,
    WebhookDelivery,
    WebhookDeliverySystem,
)

TENANT_ID = "t-webhook-test"
ORG_ID = "org-webhook-test"


# ── WebhookDelivery — sign_payload ────────────────────────────────────────────

class TestWebhookDeliverySignature:
    def _delivery(self, payload: dict, secret: str = "secret-key") -> WebhookDelivery:
        return WebhookDelivery(
            delivery_id=str(uuid.uuid4()),
            webhook_id="wh-1",
            event_type="goal.created",
            payload=payload,
            url="https://example.com/hook",
            secret=secret,
        )

    def test_signature_has_sha256_prefix(self) -> None:
        d = self._delivery({"key": "val"})
        sig = d.sign_payload()
        assert sig.startswith("sha256=")

    def test_signature_is_deterministic(self) -> None:
        d = self._delivery({"event": "goal.created", "id": "123"})
        assert d.sign_payload() == d.sign_payload()

    def test_different_payload_different_signature(self) -> None:
        d1 = self._delivery({"event": "a"})
        d2 = self._delivery({"event": "b"})
        assert d1.sign_payload() != d2.sign_payload()

    def test_different_secret_different_signature(self) -> None:
        payload = {"event": "goal.created"}
        d1 = self._delivery(payload, secret="secret-1")
        d2 = self._delivery(payload, secret="secret-2")
        assert d1.sign_payload() != d2.sign_payload()

    def test_signature_is_valid_hmac_sha256(self) -> None:
        secret = "verify-me"
        payload = {"data": "test"}
        d = self._delivery(payload, secret=secret)
        sig = d.sign_payload()
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        expected = "sha256=" + hmac.new(
            secret.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        assert sig == expected

    def test_empty_payload_does_not_crash(self) -> None:
        d = self._delivery({})
        sig = d.sign_payload()
        assert sig.startswith("sha256=")


# ── WebhookDelivery — is_dead ─────────────────────────────────────────────────

class TestWebhookDeliveryIsDead:
    def _delivery(self, attempts: int, status: str = "pending") -> WebhookDelivery:
        return WebhookDelivery(
            delivery_id=str(uuid.uuid4()),
            webhook_id="wh-1",
            event_type="goal.created",
            payload={},
            url="https://example.com/hook",
            secret="s",
            attempts=attempts,
            status=status,
        )

    def test_not_dead_below_max_retries(self) -> None:
        d = self._delivery(MAX_RETRIES - 1)
        assert d.is_dead is False

    def test_dead_at_max_retries(self) -> None:
        d = self._delivery(MAX_RETRIES)
        assert d.is_dead is True

    def test_dead_above_max_retries(self) -> None:
        d = self._delivery(MAX_RETRIES + 5)
        assert d.is_dead is True

    def test_status_dead_means_dead(self) -> None:
        d = self._delivery(0, status="dead")
        assert d.is_dead is True

    def test_fresh_delivery_not_dead(self) -> None:
        d = self._delivery(0, status="pending")
        assert d.is_dead is False


# ── WebhookDeliverySystem ────────────────────────────────────────────────────

class TestWebhookDeliverySystem:
    @pytest.fixture
    def system(self) -> WebhookDeliverySystem:
        return WebhookDeliverySystem(redis_client=None)

    async def _register(self, system: WebhookDeliverySystem, url: str = "https://example.com/hook") -> WebhookConfig:
        return await system.register_webhook(
            tenant_id=TENANT_ID,
            org_id=ORG_ID,
            name="Test Hook",
            url=url,
            events=["goal.created", "goal.completed"],
            secret="hook-secret",
        )

    @pytest.mark.asyncio
    async def test_register_webhook_stores_it(
        self, system: WebhookDeliverySystem
    ) -> None:
        cfg = await self._register(system)
        hooks = system._webhooks  # type: ignore[attr-defined]
        assert cfg.webhook_id in hooks

    @pytest.mark.asyncio
    async def test_register_webhook_returns_config_with_secret(
        self, system: WebhookDeliverySystem
    ) -> None:
        cfg = await self._register(system)
        assert cfg.secret  # secret auto-generated if not provided
        assert cfg.tenant_id == TENANT_ID

    @pytest.mark.asyncio
    async def test_deliver_calls_attempt(
        self, system: WebhookDeliverySystem
    ) -> None:
        cfg = await self._register(system)

        with patch.object(
            system, "_attempt_delivery", new_callable=AsyncMock, return_value=True
        ) as mock_attempt:
            await system.deliver(
                webhook_id=cfg.webhook_id,
                event_type="goal.created",
                payload={"goal_id": "g-1"},
            )
            mock_attempt.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_unknown_webhook_uses_empty_url(
        self, system: WebhookDeliverySystem
    ) -> None:
        """Delivering to non-registered webhook falls back to empty URL (no raise)."""
        delivery = await system.deliver(
            webhook_id="nonexistent-wh",
            event_type="goal.created",
            payload={"goal_id": "g-1"},
        )
        assert delivery.webhook_id == "nonexistent-wh"

    def test_list_dlq_empty_initially(self, system: WebhookDeliverySystem) -> None:
        assert system.list_dlq() == []

    def test_get_stats_returns_dict(self, system: WebhookDeliverySystem) -> None:
        stats = system.get_stats(TENANT_ID)
        assert isinstance(stats, dict)

    def test_backoff_schedule_is_ascending(self) -> None:
        for i in range(len(BACKOFF_SCHEDULE) - 1):
            assert BACKOFF_SCHEDULE[i] < BACKOFF_SCHEDULE[i + 1]

    def test_max_retries_matches_schedule_length(self) -> None:
        assert len(BACKOFF_SCHEDULE) == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_deliver_sets_signature_header(
        self, system: WebhookDeliverySystem
    ) -> None:
        """The _attempt_delivery call must include X-Signature header."""
        cfg = await self._register(system)

        async def fake_attempt(delivery: WebhookDelivery) -> bool:
            sig = delivery.sign_payload()
            assert sig.startswith("sha256=")
            return True

        with patch.object(system, "_attempt_delivery", side_effect=fake_attempt):
            await system.deliver(
                webhook_id=cfg.webhook_id,
                event_type="goal.created",
                payload={"goal_id": "g-2"},
            )
