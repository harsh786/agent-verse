"""Immutable audit trail — append-only log of all governed actions.

Records are stored per-tenant and are queryable by goal_id, tool_name, or
date range. The append-only constraint is enforced structurally: there is
no delete or update method.

In production this is backed by an append-only PostgreSQL table with an
immutability trigger; this in-memory version is used in tests.

When ``db_session_factory`` is supplied, writes are persisted to PostgreSQL:
``record_async`` awaits the write (and raises ``AuditWriteError`` if it cannot be
stored); ``record`` tracks its write task so ``flush`` can await it.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

from app.governance.permissions import ActionLevel
from app.observability.logging import get_logger
from app.tenancy.context import TenantContext

_log = get_logger(__name__)

_AUDIT_INSERT_SQL = """
    INSERT INTO audit_log (
        id, tenant_id, goal_id, tool_name, action_level, outcome, step_id,
        approver, note, ip_address, user_agent, api_key_id, request_id, connector_id
    ) VALUES (
        :id, :tenant_id, :goal_id, :tool_name, :action_level, :outcome, :step_id,
        :approver, :note, :ip_address, :user_agent, :api_key_id, :request_id, :connector_id
    )
    ON CONFLICT (id) DO NOTHING
"""


class AuditQueryUnavailableError(RuntimeError):
    """The authoritative (DB) audit store could not be read.

    Raised instead of silently serving this replica's in-memory cache: that cache
    only holds what THIS process wrote/warmed, so returning it as the answer to an
    audit query presents partial data as the complete, authoritative trail.
    """


def _durable_audit_required() -> bool:
    """Outside development an audit record must reach Postgres to count."""
    from app.core.config import get_settings

    return str(get_settings().environment).strip().lower() != "development"


@dataclass
class AuditEvent:
    goal_id: str
    tool_name: str
    action_level: ActionLevel
    outcome: str
    step_id: str = ""
    approver: str | None = None
    note: str = ""
    event_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex)
    # SOC2-required fields
    ip_address: str | None = None
    user_agent: str | None = None
    api_key_id: str | None = None
    request_id: str | None = None
    connector_id: str | None = None
    auth_type: str | None = None


class AuditWriteError(RuntimeError):
    """An audit event could not be stored durably (after retries)."""


class AuditPersistenceError(AuditWriteError):
    """A durable audit record could not be committed (see :meth:`AuditLog.record_durable`).

    Lets a call path that must not succeed unaudited refuse (503) instead of
    returning 200 with no trail.
    """


class AuditFieldTooLongError(AuditWriteError):
    """An audit value is wider than its ``audit_log`` column (P4-2).

    Raised BEFORE the INSERT and never retried: a deterministic overflow must
    surface loudly with the field name instead of being truncated (which would
    corrupt an id) or retried three times into a generic DB error.
    """


# Widths of the bounded ``audit_log`` columns (migration d4e7a2c9b1f3 widened
# id/tenant_id to 64). ``note`` is TEXT and unbounded.
AUDIT_COLUMN_WIDTHS: dict[str, int] = {
    "id": 64,
    "tenant_id": 64,
    "goal_id": 64,
    "tool_name": 200,
    "action_level": 20,
    "outcome": 100,
    "step_id": 64,
    "approver": 200,
    "ip_address": 45,
    "user_agent": 500,
    "api_key_id": 64,
    "request_id": 64,
    "connector_id": 64,
}


def check_audit_widths(row: dict[str, Any]) -> None:
    """Raise :class:`AuditFieldTooLongError` when a value overflows its column."""
    for column, width in AUDIT_COLUMN_WIDTHS.items():
        value = row.get(column)
        if value is not None and len(str(value)) > width:
            raise AuditFieldTooLongError(
                f"audit_log.{column} is {len(str(value))} chars; the column holds {width}"
            )


def _audit_row(event: AuditEvent, tenant_id: str) -> dict[str, Any]:
    return {
        "id": event.event_id,
        "tenant_id": tenant_id,
        "goal_id": event.goal_id,
        "tool_name": event.tool_name,
        "action_level": event.action_level.value,
        "outcome": event.outcome,
        "step_id": event.step_id or "",
        "approver": event.approver,
        "note": event.note,
        "ip_address": event.ip_address,
        "user_agent": event.user_agent,
        "api_key_id": event.api_key_id,
        "request_id": event.request_id,
        "connector_id": event.connector_id,
    }


class AuditLog:
    """Append-only audit log: PostgreSQL is the source of truth.

    ``record_async`` awaits the INSERT (retrying transient failures) and raises
    :class:`AuditWriteError` when the event could not be stored, so callers can
    fail closed. ``record`` is the sync entry point for code that cannot await:
    its write task is strongly referenced and retried, and ``flush`` awaits every
    pending write — Celery workers flush before their per-task loop closes, the
    API flushes on shutdown — so a write is never silently orphaned.
    """

    def __init__(
        self,
        db_session_factory: Any = None,
        *,
        write_attempts: int = 3,
        retry_base_delay: float = 0.2,
        cache_per_tenant: int = 1000,
        max_cached_tenants: int = 1000,
        siem_outbox: bool | None = None,
    ) -> None:
        # Durable SIEM forwarding (AUDIT-06): each DB write also enqueues an
        # ``audit_siem_outbox`` row in the same transaction. ``None`` = enabled
        # iff a SIEM is configured (SIEM_TYPE).
        if siem_outbox is None:
            from app.governance.siem_outbox import siem_outbox_enabled

            siem_outbox = siem_outbox_enabled()
        self._siem_outbox = bool(siem_outbox)
        # Bounded warm cache (AUDIT-04): Postgres is the trail; this holds at most
        # ``cache_per_tenant`` recent events for the ``max_cached_tenants`` most
        # recently written tenants.
        self._cache_per_tenant = max(1, cache_per_tenant)
        self._max_cached_tenants = max(1, max_cached_tenants)
        self._log: OrderedDict[str, deque[AuditEvent]] = OrderedDict()
        self._db = db_session_factory
        self._write_attempts = max(1, write_attempts)
        self._retry_base_delay = max(0.0, retry_base_delay)
        # Strong references to in-flight ``record`` writes (an unreferenced task
        # can be garbage-collected mid-flight) so ``flush`` can await them.
        self._pending: set[asyncio.Task[None]] = set()
        self._lost_writes = 0
        # Optional SIEM forwarder — when wired, every recorded event is also
        # enqueued for batched delivery to the configured SIEM platform.
        self._siem_forwarder: Any = None

    def set_siem_forwarder(self, forwarder: Any) -> None:
        """Attach a ``SIEMForwarder`` so recorded events are forwarded to SIEM.

        Wired from ``create_app``'s lifespan after the SIEM adapter is built.
        Passing ``None`` detaches forwarding.
        """
        self._siem_forwarder = forwarder

    @property
    def pending_writes(self) -> int:
        """Number of ``record`` writes not yet finished."""
        return len(self._pending)

    def _tenant_cache(self, tenant_id: str) -> deque[AuditEvent]:
        events = self._log.get(tenant_id)
        if events is None:
            events = deque(maxlen=self._cache_per_tenant)
            self._log[tenant_id] = events
            while len(self._log) > self._max_cached_tenants:
                self._log.popitem(last=False)
        else:
            self._log.move_to_end(tenant_id)
        return events

    def _cache(self, event: AuditEvent, tenant_id: str) -> None:
        self._tenant_cache(tenant_id).append(event)
        # Forward to SIEM (non-blocking, never raises — protects the write path).
        if self._siem_forwarder is not None:
            try:
                self._siem_forwarder.enqueue(self._to_siem_event(event, tenant_id))
            except Exception as exc:
                _log.warning("audit_siem_enqueue_failed", error=str(exc))

    def record(self, event: AuditEvent, *, tenant_ctx: TenantContext) -> None:
        """Record an event from sync code; the DB write is tracked, not orphaned.

        Prefer :meth:`record_async` wherever the caller can await. The write
        scheduled here is retried, strongly referenced, and awaited by
        :meth:`flush`; a write that finally fails is logged as an error
        (``audit_write_lost``) and counted.
        """
        tenant_id = tenant_ctx.tenant_id
        self._cache(event, tenant_id)
        if self._db is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop to run the write on: say so loudly instead of dropping it.
            self._lost_writes += 1
            _log.error("audit_write_lost", reason="no_running_loop", event_id=event.event_id)
            return
        task = loop.create_task(self._tracked_write(event, tenant_id))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def record_async(self, event: AuditEvent, *, tenant_ctx: TenantContext) -> None:
        """Record an event and await its durable write.

        Raises :class:`AuditWriteError` when a DB is configured and the event
        could not be stored after retries.
        """
        tenant_id = tenant_ctx.tenant_id
        self._cache(event, tenant_id)
        if self._db is None:
            return
        await self._persist_with_retry(event, tenant_id)

    async def flush(self, timeout: float | None = 30.0) -> int:
        """Await every pending ``record`` write; return how many were lost."""
        lost_before = self._lost_writes
        while self._pending:
            batch = list(self._pending)
            done, not_done = await asyncio.wait(batch, timeout=timeout)
            for task in not_done:
                task.cancel()
                self._pending.discard(task)
            if not_done:
                self._lost_writes += len(not_done)
                _log.error("audit_write_lost", reason="flush_timeout", count=len(not_done))
            for task in done:
                self._pending.discard(task)
        return self._lost_writes - lost_before

    async def _tracked_write(self, event: AuditEvent, tenant_id: str) -> None:
        try:
            await self._persist_with_retry(event, tenant_id)
        except AuditWriteError as exc:
            self._lost_writes += 1
            _log.error(
                "audit_write_lost",
                reason="db_write_failed",
                event_id=event.event_id,
                tenant_id=tenant_id,
                error=str(exc.__cause__ or exc),
            )

    async def _persist_with_retry(self, event: AuditEvent, tenant_id: str) -> None:
        # Deterministic overflow: refuse loudly, never truncate, never retry.
        check_audit_widths(_audit_row(event, tenant_id))
        last_exc: Exception | None = None
        for attempt in range(self._write_attempts):
            try:
                await self._db_record(event, tenant_id)
                return
            except Exception as exc:
                last_exc = exc
                _log.warning(
                    "audit_write_retry",
                    attempt=attempt + 1,
                    event_id=event.event_id,
                    error=str(exc),
                )
                if attempt + 1 < self._write_attempts and self._retry_base_delay:
                    await asyncio.sleep(self._retry_base_delay * (2**attempt))
        raise AuditWriteError(f"audit event {event.event_id} not stored") from last_exc

    @staticmethod
    def _to_siem_event(event: AuditEvent, tenant_id: str) -> dict[str, Any]:
        """Map an :class:`AuditEvent` to the flat dict shape SIEM adapters read.

        Adapters (Splunk/Elasticsearch/Datadog/CEF/LEEF/Webhook) consume keys
        like ``event_type``, ``created_at``, ``action``, ``status`` and
        ``metadata.severity``; this projects the audit record onto that shape.
        """
        from datetime import UTC, datetime

        return {
            "id": event.event_id,
            "tenant_id": tenant_id,
            "event_type": "audit.tool_execution",
            "resource_type": "tool",
            "resource_id": event.tool_name,
            "action": event.tool_name or event.action_level.value,
            "status": event.outcome,
            "created_at": datetime.now(UTC).isoformat(),
            "goal_id": event.goal_id,
            "request_id": event.request_id,
            "ip_address": event.ip_address,
            "user_agent": event.user_agent,
            "actor_label": event.approver or event.api_key_id,
            "api_key_id": event.api_key_id,
            "metadata": {
                "step_id": event.step_id,
                "note": event.note,
                "action_level": event.action_level.value,
                "connector_id": event.connector_id,
                "auth_type": event.auth_type,
            },
        }

    async def record_durable(self, event: AuditEvent, *, tenant_ctx: TenantContext) -> None:
        """:meth:`record_async`, plus: without a DB the event only counts in development.

        For call paths that must not succeed unaudited (code execution, outbound
        email, workspace writes/deletes): raises :class:`AuditPersistenceError`
        (an :class:`AuditWriteError`) when the row was not committed, or when no
        DB is wired outside development (in-memory mode would "audit" into a
        process dict that no other replica, and no restart, ever sees).
        """
        if self._db is None and _durable_audit_required():
            raise AuditPersistenceError("audit store unavailable (no database configured)")
        try:
            await self.record_async(event, tenant_ctx=tenant_ctx)
        except AuditWriteError as exc:
            _log.error(
                "audit_durable_record_failed",
                tool_name=event.tool_name,
                tenant_id=tenant_ctx.tenant_id,
                error=str(exc.__cause__ or exc)[:200],
            )
            raise AuditPersistenceError(str(exc)) from exc

    async def _db_record(self, event: AuditEvent, tenant_id: str) -> None:
        """INSERT one event, idempotent on ``id``: a retry after an unknown commit
        outcome never duplicates the row or trips the immutability trigger."""
        if self._db is None:
            return
        from opentelemetry import trace as _trace
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        _tracer = _trace.get_tracer(__name__)
        with _tracer.start_as_current_span("governance.audit.db_record") as span:
            span.set_attribute("tenant_id", tenant_id)
            span.set_attribute("tool_name", event.tool_name or "")
            span.set_attribute("outcome", event.outcome)
            async with self._db() as session, session.begin():  # noqa: SIM117
                async with sqlalchemy_rls_context(session, tenant_id):
                    inserted = await session.execute(
                        text(_AUDIT_INSERT_SQL), _audit_row(event, tenant_id)
                    )
                    # Same transaction: the SIEM copy exists iff the audit row does.
                    # A retried INSERT that hit ON CONFLICT enqueues nothing twice.
                    if self._siem_outbox and getattr(inserted, "rowcount", 1) != 0:
                        import json

                        await session.execute(
                            text(
                                "INSERT INTO audit_siem_outbox (tenant_id, audit_id, payload) "
                                "VALUES (:tid, :aid, CAST(:payload AS jsonb))"
                            ),
                            {
                                "tid": tenant_id,
                                "aid": event.event_id,
                                "payload": json.dumps(
                                    self._to_siem_event(event, tenant_id), default=str
                                ),
                            },
                        )

    def query(
        self,
        *,
        tenant_ctx: TenantContext,
        goal_id: str | None = None,
        tool_name: str | None = None,
        limit: int = 1000,
    ) -> list[AuditEvent]:
        events: list[AuditEvent] = list(self._log.get(tenant_ctx.tenant_id, ()))
        if goal_id is not None:
            events = [e for e in events if e.goal_id == goal_id]
        if tool_name is not None:
            events = [e for e in events if e.tool_name == tool_name]
        return list(events)[:limit]

    async def query_db(
        self,
        *,
        tenant_ctx: TenantContext,
        goal_id: str | None = None,
        tool_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
        start_time: str | None = None,
        end_time: str | None = None,
        outcome: str | None = None,
        q: str | None = None,
    ) -> list[AuditEvent]:
        """Read audit events directly from PostgreSQL with full filter + pagination.

        This is the production path — always reads from DB, never from the
        in-memory cache. The in-memory cache is used only when no DB is
        configured at all (tests / single-process dev); a configured DB that
        errors raises :class:`AuditQueryUnavailableError`.
        """
        if self._db is None:
            return self.query(
                tenant_ctx=tenant_ctx,
                goal_id=goal_id,
                tool_name=tool_name,
                limit=limit,
            )

        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            conditions = ["tenant_id = :tid"]
            params: dict[str, Any] = {
                "tid": tenant_ctx.tenant_id,
                "limit": limit,
                "offset": offset,
            }

            if goal_id:
                conditions.append("goal_id = :gid")
                params["gid"] = goal_id
            if tool_name:
                conditions.append("tool_name = :tname")
                params["tname"] = tool_name
            if start_time:
                conditions.append("created_at >= CAST(:start_time AS timestamptz)")
                params["start_time"] = start_time
            if end_time:
                conditions.append("created_at <= CAST(:end_time AS timestamptz)")
                params["end_time"] = end_time
            if outcome:
                conditions.append("outcome = :outcome")
                params["outcome"] = outcome
            if q:
                # Server-side free-text over the human-meaningful columns, so search
                # covers the WHOLE dataset (not just one page the client loaded).
                conditions.append(
                    "(note ILIKE :q OR tool_name ILIKE :q OR goal_id ILIKE :q OR outcome ILIKE :q)"
                )
                params["q"] = f"%{q}%"

            where_clause = " AND ".join(conditions)
            sql = f"""
                SELECT id, goal_id, tool_name, action_level, outcome,
                       step_id, approver, note, created_at,
                       ip_address, user_agent, api_key_id, request_id, connector_id
                FROM audit_log
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """

            async with self._db() as session, sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                result = await session.execute(text(sql), params)
                rows = result.fetchall()

            events: list[AuditEvent] = []
            for row in rows:
                try:
                    level = ActionLevel(row[3]) if row[3] else ActionLevel.ALLOW
                except ValueError:
                    level = ActionLevel.ALLOW_LOG

                # The SOC2 attribution columns were written but never read back.
                extra = list(row[9:14]) + [None] * (5 - len(row[9:14]))
                events.append(
                    AuditEvent(
                        event_id=row[0],
                        goal_id=row[1] or "",
                        tool_name=row[2] or "",
                        action_level=level,
                        outcome=row[4] or "",
                        step_id=row[5] or "",
                        approver=row[6],
                        note=row[7] or "",
                        ip_address=extra[0],
                        user_agent=extra[1],
                        api_key_id=extra[2],
                        request_id=extra[3],
                        connector_id=extra[4],
                    )
                )

            return events

        except Exception as exc:
            # No per-replica fallback: the in-memory cache is partial (only this
            # process's writes) and would be served as the authoritative trail.
            _log.warning("audit_query_db_failed", error=str(exc))
            raise AuditQueryUnavailableError("audit store unavailable") from exc

    async def sync_from_db(self, *, tenant_id: str | None = None) -> int:
        """Warm a BOUNDED set of recent audit entries into memory.

        The audit log is append-only and unbounded; loading all of it OOMs at
        scale (distributed-scale audit X5). Reads are served from the DB by
        ``query_db``, so this in-memory ``_log`` is only a warm cache — cap the
        load to the most-recent rows and dedup in O(N). Returns rows loaded; 0 when
        no ``db_session_factory`` is configured.
        """
        if self._db is None:
            return 0
        try:
            from sqlalchemy import select

            from app.db.models.governance import AuditLog as AuditLogModel

            loaded = 0
            async with self._db() as session:
                q = select(AuditLogModel).order_by(AuditLogModel.created_at.desc()).limit(10_000)
                if tenant_id:
                    q = q.where(AuditLogModel.tenant_id == tenant_id)
                result = await session.execute(q)
                rows = result.scalars().all()
                # Build the per-tenant dedup sets once (was O(N^2) per-row rebuild).
                existing_by_tenant: dict[str, set[str]] = {
                    t: {e.event_id for e in evs} for t, evs in self._log.items()
                }
                for row in rows:
                    events = self._tenant_cache(row.tenant_id)
                    existing_ids = existing_by_tenant.setdefault(row.tenant_id, set())
                    if row.id not in existing_ids:
                        existing_ids.add(row.id)
                        try:
                            level = ActionLevel(row.action_level)
                        except ValueError:
                            level = ActionLevel.ALLOW_LOG
                        evt = AuditEvent(
                            goal_id=row.goal_id,
                            tool_name=row.tool_name,
                            action_level=level,
                            outcome=row.outcome,
                            step_id=row.step_id or "",
                            approver=row.approver,
                            note=row.note or "",
                            event_id=row.id,
                        )
                        events.append(evt)
                        loaded += 1
            return loaded
        except Exception as exc:
            _log.warning("DB audit sync failed: %s", exc)
            return 0
