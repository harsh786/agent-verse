"""Deliver the coordination outbox (COORD-OUTBOX).

Session transitions, handoffs and transcript appends write an outbox row in the
same transaction as the state change, but nothing ever delivered those rows: they
stayed ``pending`` forever. The dispatcher closes that gap:

1. a cross-tenant scan (maintenance / BYPASSRLS role) finds tenants with due rows
   — pending and available, or claimed by a worker whose claim went stale;
2. per tenant, ``PostgresOutboxRepository.claim`` takes an exclusive claim
   (``FOR UPDATE SKIP LOCKED`` under the tenant's RLS context), so concurrent
   dispatchers on several replicas never deliver the same row twice at once;
3. ``OutboxDelivery`` publishes each envelope to the tenant+session Redis stream
   and marks it ``published`` only after the transport accepted it; a failure is
   retried with deterministic exponential backoff and dead-lettered after the
   attempt budget.

Fail closed: without a Redis transport nothing is claimed (rows stay pending and
are delivered on a later tick) — never marked delivered without a publish.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import and_, distinct, or_, select

from app.coordination.outbox import OutboxDelivery, PostgresOutboxRepository, StreamPublisher
from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import system_session

logger = structlog.get_logger(__name__)


async def tenants_with_due_outbox(
    system_factory: Any, *, claim_ttl_seconds: int = 60, limit: int = 200
) -> list[str]:
    """Tenants holding deliverable outbox rows (cross-tenant scan, maintenance role)."""
    table = COORDINATION_TABLES["coordination_outbox"]
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=claim_ttl_seconds)
    async with system_factory() as db, db.begin(), system_session(db):
        rows = await db.execute(
            select(distinct(table.c.tenant_id))
            .where(
                or_(
                    and_(table.c.state == "pending", table.c.available_at <= now),
                    and_(table.c.state == "claimed", table.c.claimed_at <= stale_before),
                )
            )
            .limit(limit)
        )
        return [str(tenant_id) for (tenant_id,) in rows.all()]


class CoordinationOutboxDispatcher:
    def __init__(
        self,
        *,
        session_factory: Any,
        system_session_factory: Any,
        publisher: Callable[[], StreamPublisher | None],
        batch_size: int = 100,
        max_tenants: int = 200,
        max_attempts: int = 8,
        claim_ttl_seconds: int = 60,
        owner: str | None = None,
    ) -> None:
        self._sessions = session_factory
        self._system = system_session_factory
        self._publisher = publisher
        self._batch = batch_size
        self._max_tenants = max_tenants
        self._max_attempts = max_attempts
        self._claim_ttl = claim_ttl_seconds
        self._owner = owner or f"outbox-{uuid.uuid4().hex[:12]}"

    async def dispatch_once(self) -> dict[str, Any]:
        publisher = self._publisher()
        if publisher is None:
            logger.warning("coordination_outbox_no_transport")
            return {"status": "skipped", "reason": "no_stream_transport", "delivered": 0}
        tenants = await tenants_with_due_outbox(
            self._system, claim_ttl_seconds=self._claim_ttl, limit=self._max_tenants
        )
        delivered = 0
        failed_tenants = 0
        for tenant_id in tenants:
            delivery = OutboxDelivery(
                PostgresOutboxRepository(
                    self._sessions, tenant_id=tenant_id, claim_ttl_seconds=self._claim_ttl
                ),
                publisher,
                max_attempts=self._max_attempts,
            )
            try:
                delivered += await delivery.deliver(owner=self._owner, limit=self._batch)
            except Exception as exc:  # one tenant's failure must not stall the others
                failed_tenants += 1
                logger.warning(
                    "coordination_outbox_tenant_failed", tenant_id=tenant_id, error=str(exc)[:200]
                )
        return {
            "status": "ok",
            "tenants": len(tenants),
            "delivered": delivered,
            "failed_tenants": failed_tenants,
        }


__all__ = ["CoordinationOutboxDispatcher", "tenants_with_due_outbox"]
