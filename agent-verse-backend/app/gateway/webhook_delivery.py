"""QA7 — Webhook Delivery Guarantees (at-least-once delivery).

Guarantees:
  - At-least-once delivery (never silent drop)
  - Per-payload idempotency via X-Delivery-ID header
  - Exponential backoff: 1s → 5s → 30s → 5min → 30min
  - Max 5 retries before DLQ
  - HMAC-SHA256 signature on every delivery (X-Signature header)
  - Receiver MUST respond 2xx within 10s or delivery retried

Dead Letter Queue:
  - Entries after max retries
  - Admin can retry or dismiss
  - Status: pending | delivered | failed | dead
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.observability.logging import get_logger

_log = get_logger(__name__)

# Exponential backoff schedule (seconds)
BACKOFF_SCHEDULE: list[int] = [1, 5, 30, 300, 1800]
MAX_RETRIES = 5
DELIVERY_TIMEOUT_SECONDS = 10.0


@dataclass
class WebhookConfig:
    """A registered outbound webhook."""

    webhook_id: str
    tenant_id: str
    org_id: str
    name: str
    url: str
    secret: str
    events: list[str] = field(default_factory=lambda: ["*"])
    active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class WebhookDelivery:
    """Per spec QA7 — a single webhook delivery attempt."""

    delivery_id: str
    webhook_id: str
    event_type: str
    payload: dict[str, Any]
    url: str
    secret: str

    # Retry state
    attempts: int = 0
    max_attempts: int = MAX_RETRIES
    next_retry_at: datetime | None = None
    backoff_seconds: list[int] = field(default_factory=lambda: list(BACKOFF_SCHEDULE))

    # Status
    status: str = "pending"  # pending | delivered | failed | dead
    last_response_code: int | None = None
    last_error: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def sign_payload(self) -> str:
        """Compute HMAC-SHA256 signature for payload."""
        body = json.dumps(self.payload, sort_keys=True, ensure_ascii=False)
        return "sha256=" + hmac.new(self.secret.encode(), body.encode(), hashlib.sha256).hexdigest()

    @property
    def is_dead(self) -> bool:
        return self.attempts >= self.max_attempts or self.status == "dead"


class WebhookDeliverySystem:
    """
    QA7 — At-least-once webhook delivery with exponential backoff.

    Usage:
        system = WebhookDeliverySystem()
        await system.deliver(
            webhook_id="wh_123",
            event_type="org.mission.completed",
            payload={"mission_id": "...", "org_id": "..."},
            url="https://your-server.com/webhook",
            secret="your-hmac-secret",
        )
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis = redis_client
        self._http = httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_SECONDS)
        self._pending: dict[str, WebhookDelivery] = {}
        self._dlq: list[WebhookDelivery] = []
        self._webhooks: dict[str, WebhookConfig] = {}

    async def register_webhook(
        self,
        tenant_id: str,
        org_id: str,
        name: str,
        url: str,
        events: list[str],
        secret: str | None = None,
    ) -> WebhookConfig:
        """Register a new outbound webhook."""
        import secrets as _secrets

        wh = WebhookConfig(
            webhook_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            org_id=org_id,
            name=name,
            url=url,
            secret=secret or _secrets.token_hex(32),
            events=events,
        )
        self._webhooks[wh.webhook_id] = wh
        _log.info("webhook.registered", webhook_id=wh.webhook_id, url=url)
        return wh

    async def deliver(
        self,
        webhook_id: str,
        event_type: str,
        payload: dict[str, Any],
        url: str | None = None,
        secret: str | None = None,
    ) -> WebhookDelivery:
        """
        Queue a webhook delivery.
        Returns a WebhookDelivery that will be retried until success or DLQ.
        """
        wh = self._webhooks.get(webhook_id)
        delivery_url = url or (wh.url if wh else "")
        delivery_secret = secret or (wh.secret if wh else "")

        delivery = WebhookDelivery(
            delivery_id=str(uuid.uuid4()),
            webhook_id=webhook_id,
            event_type=event_type,
            payload={
                **payload,
                "_delivery_id": str(uuid.uuid4()),  # idempotency key
                "_event_type": event_type,
                "_timestamp": datetime.now(UTC).isoformat(),
            },
            url=delivery_url,
            secret=delivery_secret,
        )
        self._pending[delivery.delivery_id] = delivery

        # Attempt delivery immediately
        await self._attempt_delivery(delivery)
        return delivery

    async def _attempt_delivery(self, delivery: WebhookDelivery) -> bool:
        """Attempt one delivery. Returns True on success."""
        if not delivery.url:
            delivery.status = "failed"
            delivery.last_error = "No URL configured"
            return False

        delivery.attempts += 1
        sig = delivery.sign_payload()
        body = json.dumps(delivery.payload, sort_keys=True, ensure_ascii=False)

        try:
            resp = await self._http.post(
                delivery.url,
                content=body.encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-Delivery-ID": delivery.delivery_id,
                    "X-Event-Type": delivery.event_type,
                    "X-Timestamp": datetime.now(UTC).isoformat(),
                    "X-Signature": sig,
                },
                timeout=DELIVERY_TIMEOUT_SECONDS,
            )
            delivery.last_response_code = resp.status_code

            if 200 <= resp.status_code < 300:
                delivery.status = "delivered"
                delivery.delivered_at = datetime.now(UTC)
                del self._pending[delivery.delivery_id]
                _log.info(
                    "webhook.delivered",
                    delivery_id=delivery.delivery_id,
                    url=delivery.url,
                    attempt=delivery.attempts,
                )
                return True

            # Non-2xx → schedule retry
            delivery.last_error = f"HTTP {resp.status_code}"

        except httpx.TimeoutException:
            delivery.last_error = f"Timeout after {DELIVERY_TIMEOUT_SECONDS}s"
        except Exception as exc:
            delivery.last_error = str(exc)

        # Schedule retry or move to DLQ
        return await self._schedule_retry(delivery)

    async def _schedule_retry(self, delivery: WebhookDelivery) -> bool:
        if delivery.is_dead:
            delivery.status = "dead"
            self._dlq.append(delivery)
            del self._pending[delivery.delivery_id]
            _log.warning(
                "webhook.dead_letter",
                delivery_id=delivery.delivery_id,
                attempts=delivery.attempts,
                error=delivery.last_error,
            )
            return False

        delay = delivery.backoff_seconds[
            min(delivery.attempts - 1, len(delivery.backoff_seconds) - 1)
        ]
        _log.info(
            "webhook.retry_scheduled",
            delivery_id=delivery.delivery_id,
            attempt=delivery.attempts,
            next_retry_seconds=delay,
        )

        # Schedule async retry
        asyncio.create_task(self._delayed_retry(delivery, delay))
        return False

    async def _delayed_retry(self, delivery: WebhookDelivery, delay_seconds: int) -> None:
        await asyncio.sleep(delay_seconds)
        if delivery.delivery_id in self._pending:
            await self._attempt_delivery(delivery)

    # ── DLQ management ─────────────────────────────────────────────────────────

    def list_dlq(self, org_id: str | None = None) -> list[WebhookDelivery]:
        if org_id:
            # Filter by org (payload contains org_id)
            return [d for d in self._dlq if d.payload.get("org_id") == org_id]
        return list(self._dlq)

    async def retry_from_dlq(self, delivery_id: str) -> bool:
        for i, d in enumerate(self._dlq):
            if d.delivery_id == delivery_id:
                self._dlq.pop(i)
                d.status = "pending"
                d.attempts = 0
                self._pending[d.delivery_id] = d
                await self._attempt_delivery(d)
                return True
        return False

    async def dismiss_dlq(self, delivery_id: str) -> bool:
        for i, d in enumerate(self._dlq):
            if d.delivery_id == delivery_id:
                self._dlq.pop(i)
                return True
        return False

    def get_stats(self, tenant_id: str | None = None) -> dict[str, int]:
        """Delivery statistics."""
        return {
            "pending": len(self._pending),
            "dead": len(self._dlq),
        }

    async def close(self) -> None:
        await self._http.aclose()


# Global instance
webhook_delivery_system = WebhookDeliverySystem()
