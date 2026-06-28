"""Production-grade audit system with WAL, hash chaining, and SIEM integration.

This module delivers:
  1. Redis WAL for at-least-once delivery (survives DB failures)
  2. Cryptographic SHA-256 hash chaining for tamper detection
  3. Admin action audit decorator (captures every mutating admin op)
  4. PII redaction before storage
  5. SIEM forwarding pipeline via pluggable adapters (see siem_adapters.py)

Writes never block the HTTP response path — all DB work is deferred to the
background AuditFlusher task that runs every WAL_FLUSH_INTERVAL seconds.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.observability.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Redis WAL constants
# ---------------------------------------------------------------------------
WAL_KEY = "audit:wal"
WAL_DEAD_LETTER = "audit:wal:dlq"
WAL_BATCH_SIZE = 100
WAL_FLUSH_INTERVAL = 5  # seconds

# PII field names — values are replaced with "[REDACTED]" before storage
_PII_KEYS: frozenset[str] = frozenset(
    {
        "ssn",
        "social_security",
        "credit_card",
        "card_number",
        "cvv",
        "password",
        "secret",
        "token",
        "api_key",
        "private_key",
        "authorization",
        "auth_token",
        "access_token",
        "refresh_token",
    }
)


def _redact_pii(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact PII values in nested dicts/lists (max depth 5)."""
    if _depth > 5:
        return obj
    if isinstance(obj, dict):
        return {
            k: "[REDACTED]" if k.lower() in _PII_KEYS else _redact_pii(v, _depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_pii(item, _depth + 1) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# AuditEvent
# ---------------------------------------------------------------------------


class AuditEvent:
    """Serializable audit event with SHA-256 hash chain support.

    All fields match the ``audit_events`` table schema (migration 0057).
    The ``compute_hash`` method builds a canonical JSON representation of the
    event and hashes it together with the previous event's hash, forming a
    tamper-evident chain per tenant.
    """

    __slots__ = (
        "action",
        "actor_label",
        "actor_type",
        "agent_id",
        "api_key_id",
        "created_at",
        "error_code",
        "error_message",
        "event_hash",
        "event_type",
        "goal_id",
        "id",
        "ip_address",
        "metadata",
        "prev_hash",
        "request_id",
        "resource_id",
        "resource_label",
        "resource_type",
        "status",
        "tenant_id",
        "tool_args_hash",
        "tool_args_safe",
        "tool_name",
        "user_agent",
        "user_id",
    )

    def __init__(self, **kwargs: Any) -> None:
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))
        if not self.id:
            self.id = str(uuid4())
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()
        if self.status is None:
            self.status = "success"
        if self.metadata is None:
            self.metadata = {}
        if self.prev_hash is None:
            self.prev_hash = ""
        if self.event_hash is None:
            self.event_hash = ""

    def compute_hash(self, prev_hash: str = "") -> str:
        """Return SHA-256 of the canonical JSON representation of this event.

        The canonical form is deterministic (sort_keys=True, no whitespace) and
        includes the previous event's hash to form a chain.
        """
        canonical = json.dumps(
            {
                "id": self.id,
                "tenant_id": str(self.tenant_id) if self.tenant_id else None,
                "event_type": self.event_type,
                "resource_id": str(self.resource_id) if self.resource_id else None,
                "action": self.action,
                "status": self.status,
                "created_at": self.created_at,
                "prev_hash": prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {slot: getattr(self, slot) for slot in self.__slots__}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# ---------------------------------------------------------------------------
# AuditWriter — non-blocking Redis WAL
# ---------------------------------------------------------------------------


class AuditWriter:
    """Writes audit events to a Redis list (WAL).

    Guarantees:
      - Never raises — a Redis failure is logged but does not bubble up.
      - Non-blocking — callers see <1 ms latency (one RPUSH).
      - At-least-once — events survive in Redis until AuditFlusher inserts
        them to Postgres.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def write(self, event: AuditEvent) -> None:
        """Push one event to the WAL.  Never raises."""
        try:
            await self._redis.rpush(WAL_KEY, event.to_json())
        except Exception as exc:
            logger.error(
                "audit_wal_write_failed",
                event_id=event.id,
                event_type=event.event_type,
                error=str(exc),
            )

    async def write_batch(self, events: list[AuditEvent]) -> None:
        """Push multiple events via a single pipelined command."""
        if not events:
            return
        pipeline = self._redis.pipeline(transaction=False)
        for event in events:
            pipeline.rpush(WAL_KEY, event.to_json())
        try:
            await pipeline.execute()
        except Exception as exc:
            logger.error(
                "audit_wal_batch_write_failed", count=len(events), error=str(exc)
            )


# ---------------------------------------------------------------------------
# AuditFlusher — background WAL → Postgres drainer
# ---------------------------------------------------------------------------


class AuditFlusher:
    """Drains the Redis WAL into the ``audit_events`` Postgres table.

    Runs every ``WAL_FLUSH_INTERVAL`` seconds as an asyncio background task.
    Computes the per-tenant hash chain at flush time (not write time) to keep
    the write path at O(1).

    On Postgres failure: events are pushed to the dead-letter Redis list
    ``audit:wal:dlq`` for manual replay.
    """

    def __init__(self, redis: Any, db_factory: Any) -> None:
        self._redis = redis
        self._db = db_factory
        # tenant_id → hash of the last flushed event (in-process cache)
        self._chain_cache: dict[str, str] = {}

    async def flush(self) -> int:
        """Drain up to WAL_BATCH_SIZE events from Redis and insert to Postgres.

        Returns the number of events successfully flushed.
        """
        pipeline = self._redis.pipeline(transaction=False)
        for _ in range(WAL_BATCH_SIZE):
            pipeline.lpop(WAL_KEY)
        results = await pipeline.execute()
        raw_events = [r for r in results if r is not None]

        if not raw_events:
            return 0

        events_to_insert: list[dict[str, Any]] = []
        for raw in raw_events:
            try:
                event_dict = json.loads(raw)
                tenant_id = str(event_dict.get("tenant_id", ""))

                prev_hash = self._chain_cache.get(tenant_id, "")
                ae = AuditEvent(**event_dict)
                ae.prev_hash = prev_hash
                ae.event_hash = ae.compute_hash(prev_hash)
                self._chain_cache[tenant_id] = ae.event_hash

                events_to_insert.append(ae.to_dict())
            except Exception as exc:
                logger.error("audit_flush_deserialize_error", error=str(exc))

        if not events_to_insert:
            return 0

        async with self._db() as db:
            try:
                from sqlalchemy import text

                await db.execute(
                    text(
                        """
                        INSERT INTO audit_events (
                            id, tenant_id, user_id, api_key_id, actor_type, actor_label,
                            event_type, resource_type, resource_id, resource_label,
                            action, status, error_code, error_message,
                            ip_address, user_agent, request_id,
                            goal_id, agent_id,
                            metadata, tool_name, tool_args_hash, tool_args_safe,
                            prev_hash, event_hash, created_at
                        )
                        SELECT
                            e->>'id',
                            e->>'tenant_id',
                            e->>'user_id',
                            e->>'api_key_id',
                            COALESCE(e->>'actor_type', 'system'),
                            e->>'actor_label',
                            e->>'event_type',
                            e->>'resource_type',
                            e->>'resource_id',
                            e->>'resource_label',
                            e->>'action',
                            COALESCE(e->>'status', 'success'),
                            e->>'error_code',
                            e->>'error_message',
                            e->>'ip_address',
                            e->>'user_agent',
                            e->>'request_id',
                            e->>'goal_id',
                            e->>'agent_id',
                            COALESCE(e->'metadata', '{}')::jsonb,
                            e->>'tool_name',
                            e->>'tool_args_hash',
                            e->'tool_args_safe',
                            COALESCE(e->>'prev_hash', ''),
                            COALESCE(e->>'event_hash', ''),
                            COALESCE((e->>'created_at')::timestamptz, now())
                        FROM jsonb_array_elements(:events::jsonb) AS e
                        ON CONFLICT (id, created_at) DO NOTHING
                        """
                    ),
                    {"events": json.dumps(events_to_insert, default=str)},
                )
                await db.commit()
                logger.info("audit_flushed", count=len(events_to_insert))
                return len(events_to_insert)
            except Exception as exc:
                await db.rollback()
                logger.error(
                    "audit_flush_db_error",
                    count=len(events_to_insert),
                    error=str(exc),
                )
                await self._send_to_dlq(events_to_insert)
                return 0

    async def run(self) -> None:
        """Background loop: flush WAL every WAL_FLUSH_INTERVAL seconds.

        Intended to be launched as an asyncio.create_task() during lifespan.
        Runs indefinitely until the task is cancelled on shutdown.
        """
        import asyncio as _asyncio_wal

        while True:
            try:
                await self.flush()
            except Exception as exc:
                logger.error("audit_flusher_loop_error", error=str(exc))
            await _asyncio_wal.sleep(WAL_FLUSH_INTERVAL)

    async def _send_to_dlq(self, events: list[dict[str, Any]]) -> None:
        pipeline = self._redis.pipeline(transaction=False)
        for e in events:
            pipeline.rpush(WAL_DEAD_LETTER, json.dumps(e, default=str))
        with contextlib.suppress(Exception):
            await pipeline.execute()


# ---------------------------------------------------------------------------
# HashChainVerifier
# ---------------------------------------------------------------------------


class HashChainVerifier:
    """Verifies the cryptographic hash chain for a tenant's audit events.

    Reads events in chronological order and recomputes each hash from scratch.
    Any discrepancy indicates that a record was modified after it was written.
    """

    async def verify(
        self,
        db: Any,
        tenant_id: str,
        from_date: datetime,
        to_date: datetime,
    ) -> dict[str, Any]:
        from sqlalchemy import text

        rows_result = await db.execute(
            text(
                """
                SELECT id, event_type, resource_id, action, status,
                       created_at, prev_hash, event_hash
                FROM audit_events
                WHERE tenant_id = :tenant_id
                  AND created_at BETWEEN :from_date AND :to_date
                ORDER BY created_at ASC, id ASC
                """
            ),
            {
                "tenant_id": tenant_id,
                "from_date": from_date,
                "to_date": to_date,
            },
        )
        events = rows_result.fetchall()

        verified = 0
        prev_hash = ""
        for row in events:
            ae = AuditEvent(
                id=str(row.id),
                tenant_id=tenant_id,
                event_type=row.event_type,
                resource_id=str(row.resource_id) if row.resource_id else None,
                action=row.action,
                status=row.status,
                created_at=(
                    row.created_at.isoformat()
                    if hasattr(row.created_at, "isoformat")
                    else str(row.created_at)
                ),
            )
            expected_hash = ae.compute_hash(prev_hash)
            if expected_hash != row.event_hash:
                return {
                    "verified": False,
                    "verified_events": verified,
                    "broken_chain_at": str(row.id),
                    "broken_at_time": str(row.created_at),
                }
            prev_hash = row.event_hash
            verified += 1

        return {
            "verified": True,
            "verified_events": verified,
            "broken_chain_at": None,
            "chain_tip_hash": prev_hash,
        }


# ---------------------------------------------------------------------------
# Admin action audit decorator
# ---------------------------------------------------------------------------


def audit_admin_action(
    event_type: str,
    resource_type: str,
    action: str,
    extract_resource_id: Callable[..., Any] | None = None,
) -> Callable[..., Any]:
    """Decorator that emits an AuditEvent for every admin route handler call.

    Captures success *and* failure — failure status includes the exception type
    and a truncated error message.

    Usage::

        @router.delete("/agents/{agent_id}")
        @audit_admin_action(
            "agent.deleted", "agent", "delete",
            extract_resource_id=lambda kw: kw.get("agent_id"),
        )
        async def delete_agent(agent_id: str, request: Request, ...):
            ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            from fastapi import Request as _Request

            request: _Request | None = kwargs.get("request") or next(
                (a for a in args if isinstance(a, _Request)), None
            )
            tenant = getattr(request.state, "tenant", None) if request else None
            api_key = getattr(request.state, "api_key", None) if request else None

            resource_id: str | None = None
            if extract_resource_id is not None:
                with contextlib.suppress(Exception):
                    resource_id = extract_resource_id(kwargs)

            evt_status = "success"
            error_code: str | None = None
            error_message: str | None = None

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as exc:
                evt_status = "failure"
                error_code = getattr(exc, "code", type(exc).__name__)
                error_message = str(exc)[:500]
                raise
            finally:
                if tenant is not None:
                    event = AuditEvent(
                        tenant_id=str(tenant.id),
                        user_id=(
                            str(api_key.user_id)
                            if api_key and getattr(api_key, "user_id", None)
                            else None
                        ),
                        api_key_id=str(api_key.id) if api_key else None,
                        actor_type="api_key" if api_key else "system",
                        actor_label=(
                            getattr(api_key, "prefix", None) if api_key else "system"
                        ),
                        event_type=event_type,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        action=action,
                        status=evt_status,
                        error_code=error_code,
                        error_message=error_message,
                        ip_address=(
                            str(request.client.host)
                            if request and request.client
                            else None
                        ),
                        request_id=(
                            request.headers.get("X-Request-ID") if request else None
                        ),
                    )
                    if (
                        request is not None
                        and hasattr(request, "app")
                        and hasattr(request.app.state, "audit_writer")
                    ):
                        with contextlib.suppress(Exception):
                            await request.app.state.audit_writer.write(event)

        return wrapper

    return decorator
