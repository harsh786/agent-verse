"""
Usage Metering Service
======================
Emits usage_records for goals, LLM tokens, tool calls, and storage.
Called from:
  - GoalService on goal completion (metric: goals, llm_tokens)
  - MCPClient after each tool call (metric: tool_calls)
  - CostController for cost tracking

Designed to be fire-and-forget (async tasks) so it never blocks the hot path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


class UsageSummaryUnavailableError(RuntimeError):
    """The durable usage rollup could not be read (the summary would be wrong)."""


class UsageService:
    """
    Records usage metrics to DB and/or in-memory buffer.
    DB writes are batched / fire-and-forget.
    """

    def __init__(self, db_factory: Any = None) -> None:
        self._db = db_factory
        # In-memory buffer for aggregation before DB flush
        self._buffer: list[dict[str, Any]] = []
        # Lazy-init asyncio.Lock — prevents concurrent _flush() calls from
        # double-consuming or double-inserting buffer records.
        self._flush_lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Return the asyncio.Lock, creating it lazily on first use."""
        if self._flush_lock is None:
            self._flush_lock = asyncio.Lock()
        return self._flush_lock

    async def record(
        self,
        *,
        tenant_id: str,
        metric: str,
        quantity: float,
        unit_cost_usd: float = 0.0,
        goal_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        record_id: str | None = None,
    ) -> None:
        """Record a usage event. Non-blocking — buffers and flushes async.

        *record_id* makes the record idempotent: the DB insert is ``ON CONFLICT
        (id) DO NOTHING``, so a deterministic id (e.g. one per goal completion)
        is written once even if a redelivered task records it again.
        """
        if record_id is not None and any(r["id"] == record_id for r in self._buffer):
            return
        now = datetime.now(UTC)
        record = {
            "id": record_id or uuid.uuid4().hex,
            "tenant_id": tenant_id,
            "goal_id": goal_id,
            "metric": metric,
            "quantity": quantity,
            "unit_cost_usd": unit_cost_usd,
            "total_cost_usd": quantity * unit_cost_usd,
            "period_start": now.isoformat(),
            "metadata": metadata or {},
        }
        self._buffer.append(record)

        # Flush in background when buffer reaches threshold
        if len(self._buffer) >= 50:
            _task = asyncio.create_task(self._flush())
            # Keep a reference to avoid the task being garbage-collected
            _task.add_done_callback(lambda _: None)

    async def record_goal_completion(
        self,
        *,
        tenant_id: str,
        goal_id: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        status: str | None = None,
    ) -> None:
        """Record metrics at goal completion — goals + llm_tokens.

        Idempotent per goal (deterministic record ids), and the goal's LLM cost
        is carried once: by ``llm_tokens`` when tokens were used, otherwise by
        ``goals`` (it used to be on both, doubling ``total_cost_usd``).
        """
        total_tokens = input_tokens + output_tokens
        await self.record(
            tenant_id=tenant_id,
            metric="goals",
            quantity=1,
            unit_cost_usd=0.0 if total_tokens > 0 else cost_usd,
            goal_id=goal_id,
            metadata={"status": status} if status else None,
            record_id=_goal_record_id(goal_id, "goals"),
        )
        if total_tokens > 0:
            await self.record(
                tenant_id=tenant_id,
                metric="llm_tokens",
                quantity=float(total_tokens),
                unit_cost_usd=cost_usd / max(total_tokens, 1),
                goal_id=goal_id,
                metadata={"input": input_tokens, "output": output_tokens},
                record_id=_goal_record_id(goal_id, "llm_tokens"),
            )

    async def record_tool_call(
        self,
        *,
        tenant_id: str,
        tool_name: str,
        server_id: str,
        goal_id: str | None = None,
        success: bool | None = None,
    ) -> None:
        """Record a single tool call."""
        metadata: dict[str, Any] = {"tool": tool_name, "server": server_id}
        if success is not None:
            metadata["success"] = success
        await self.record(
            tenant_id=tenant_id,
            metric="tool_calls",
            quantity=1.0,
            goal_id=goal_id,
            metadata=metadata,
        )

    async def flush(self) -> None:
        """Write everything buffered now (failures are logged and re-buffered).

        Metering callers flush eagerly: the buffer is per process, so records
        left in it were invisible to /billing/usage on every other replica and
        lost when a short-lived worker process exited.
        """
        while self._buffer and self._db is not None:
            before = len(self._buffer)
            await self._flush()
            if len(self._buffer) >= before:
                break  # nothing written (tenant failures re-buffered) — stop, retry later

    async def get_usage_summary(
        self,
        tenant_id: str,
        *,
        period_days: int = 30,
    ) -> dict[str, Any]:
        """Return usage summary for the last N days.

        Rolls up the durable ``usage_records`` table (RLS-scoped) so the summary
        reflects all persisted usage, not just whatever happens to still be in the
        in-memory buffer (which is drained on every flush). The un-flushed buffer is
        merged on top: a record lives in exactly one place — buffer or DB — so this
        never double-counts. With no DB factory (unit-test / in-memory path) the
        rollup is skipped and only the buffer is summarised. Raises
        :class:`UsageSummaryUnavailableError` when the rollup cannot be read.
        """
        summary: dict[str, float] = defaultdict(float)
        costs: dict[str, float] = defaultdict(float)

        if self._db is not None:
            try:
                from sqlalchemy import text

                from app.db.rls import sqlalchemy_rls_context

                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_id),
                ):
                    rows = (
                        await session.execute(
                            text(
                                "SELECT metric,"
                                " COALESCE(SUM(quantity), 0) AS quantity,"
                                " COALESCE(SUM(total_cost_usd), 0) AS total_cost_usd"
                                " FROM usage_records"
                                " WHERE tenant_id = :tenant_id"
                                " AND period_start > now() - make_interval(days => :days)"
                                " GROUP BY metric"
                            ),
                            {"tenant_id": tenant_id, "days": period_days},
                        )
                    ).all()
                for metric, quantity, total_cost in rows:
                    summary[str(metric)] += float(quantity)
                    costs[str(metric)] += float(total_cost)
            except Exception as exc:
                # a08-F197-04: this returned only the in-memory buffer, so
                # /billing/usage reported (near) zero usage during a DB outage
                # instead of an error. A partial number is never shown.
                logger.warning("usage_summary_db_failed", error=str(exc)[:80])
                raise UsageSummaryUnavailableError(
                    "usage records could not be read; try again shortly"
                ) from exc

        for record in self._buffer:
            if record["tenant_id"] == tenant_id:
                summary[record["metric"]] += float(record["quantity"])
                costs[record["metric"]] += float(record["total_cost_usd"])

        return {
            "tenant_id": tenant_id,
            "period_days": period_days,
            "usage": dict(summary),
            "costs": dict(costs),
            "total_cost_usd": sum(costs.values()),
        }

    async def _flush(self) -> None:
        """Flush buffered records to DB.

        Serialised by an asyncio.Lock so concurrent fire-and-forget tasks
        cannot double-consume or double-insert the same buffer slice.
        """
        if not self._buffer or self._db is None:
            return

        async with self._get_lock():
            if not self._buffer:
                return  # Another task drained the buffer while we waited for the lock

            to_flush = self._buffer[:100]
            self._buffer = self._buffer[100:]

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        # The buffer mixes tenants. usage_records is FORCE-RLS, so each tenant's
        # slice is written in its own transaction under that tenant's RLS context.
        # (This used to be one cross-tenant batch under system_session — an RLS
        # bypass inside the API process, which under the API's NOBYPASSRLS role
        # failed every flush.) A failing tenant re-buffers only its own records.
        by_tenant: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in to_flush:
            by_tenant[str(record["tenant_id"])].append(record)

        insert = text(
            "INSERT INTO usage_records"
            " (id, tenant_id, goal_id, metric, quantity,"
            " unit_cost_usd, total_cost_usd, period_start, metadata)"
            " VALUES (:id, :tenant_id, :goal_id, :metric, :quantity,"
            " :unit_cost_usd, :total_cost_usd, :period_start,"
            " CAST(:metadata AS json))"
            " ON CONFLICT (id) DO NOTHING"
        )
        failed: list[dict[str, Any]] = []
        flushed = 0
        for tenant_id, records in by_tenant.items():
            try:
                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_id),
                ):
                    await session.execute(insert, [_insert_params(r) for r in records])
                flushed += len(records)
            except Exception as exc:
                logger.warning("usage_flush_failed", tenant_id=tenant_id, error=str(exc)[:120])
                failed.extend(records)
        if flushed:
            logger.info("usage_flushed", count=flushed)
        if failed:
            # Re-add to buffer for retry
            self._buffer = failed + self._buffer


def _goal_record_id(goal_id: str, metric: str) -> str:
    """Deterministic usage_records id for a per-goal metric (written at most once)."""
    return hashlib.sha256(f"goal-usage:{goal_id}:{metric}".encode()).hexdigest()[:32]


def _insert_params(record: dict[str, Any]) -> dict[str, Any]:
    """Bind parameters for one buffered record.

    ``period_start`` is buffered as an ISO string but bound as a ``datetime``
    (asyncpg will not coerce a str into a timestamptz parameter), and metadata is
    real JSON — the previous ``str(dict).replace("'", '"')`` produced invalid JSON
    for ``None``/``True`` values or quotes inside strings.
    """
    period_start = record["period_start"]
    if isinstance(period_start, str):
        period_start = datetime.fromisoformat(period_start)
    return {
        "id": record["id"],
        "tenant_id": record["tenant_id"],
        "goal_id": record.get("goal_id"),
        "metric": record["metric"],
        "quantity": record["quantity"],
        "unit_cost_usd": record["unit_cost_usd"],
        "total_cost_usd": record["total_cost_usd"],
        "period_start": period_start,
        "metadata": json.dumps(record.get("metadata") or {}, default=str),
    }
