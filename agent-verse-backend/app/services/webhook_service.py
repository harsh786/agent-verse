"""Outbound webhook delivery with retry and dead-letter queue."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class WebhookDelivery:
    delivery_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str = ""
    url: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | delivered | failed | dead
    attempts: int = 0
    max_attempts: int = 3
    last_error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    delivered_at: str = ""


class OutboundWebhookService:
    """Delivers outbound webhooks with exponential backoff retry."""

    def __init__(self) -> None:
        self._deliveries: dict[str, WebhookDelivery] = {}
        self._dlq: list[WebhookDelivery] = []  # Dead Letter Queue

    async def deliver(
        self,
        *,
        tenant_id: str,
        url: str,
        payload: dict[str, Any],
        max_attempts: int = 3,
        secret: str = "",
    ) -> WebhookDelivery:
        """Deliver a webhook with retry. Returns delivery record."""
        delivery = WebhookDelivery(
            tenant_id=tenant_id,
            url=url,
            payload=payload,
            max_attempts=max_attempts,
        )
        self._deliveries[delivery.delivery_id] = delivery

        for attempt in range(max_attempts):
            delivery.attempts = attempt + 1
            backoff = 2 ** attempt  # 1s, 2s, 4s

            try:
                headers: dict[str, str] = {"Content-Type": "application/json"}
                if secret:
                    import hashlib
                    import hmac
                    import json
                    body = json.dumps(payload)
                    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
                    headers["X-Webhook-Signature"] = f"sha256={sig}"

                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()

                delivery.status = "delivered"
                delivery.delivered_at = datetime.now(UTC).isoformat()
                logger.info("webhook_delivered", delivery_id=delivery.delivery_id, url=url)
                return delivery

            except Exception as exc:
                delivery.last_error = str(exc)
                logger.warning(
                    "webhook_delivery_failed",
                    delivery_id=delivery.delivery_id,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(backoff)

        # All attempts exhausted → dead letter
        delivery.status = "dead"
        self._dlq.append(delivery)
        logger.error("webhook_dead_letter", delivery_id=delivery.delivery_id, url=url)
        return delivery

    def get_delivery(self, delivery_id: str) -> WebhookDelivery | None:
        return self._deliveries.get(delivery_id)

    def list_dlq(self, *, tenant_id: str) -> list[WebhookDelivery]:
        return [d for d in self._dlq if d.tenant_id == tenant_id]

    def get_deliveries(self, *, tenant_id: str) -> list[WebhookDelivery]:
        return [d for d in self._deliveries.values() if d.tenant_id == tenant_id]
