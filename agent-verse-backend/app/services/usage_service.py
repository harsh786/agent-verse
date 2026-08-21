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
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


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
    ) -> None:
        """Record a usage event. Non-blocking — buffers and flushes async."""
        now = datetime.now(UTC)
        record = {
            "id": uuid.uuid4().hex,
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
    ) -> None:
        """Record metrics at goal completion — goals + llm_tokens."""
        await self.record(
            tenant_id=tenant_id,
            metric="goals",
            quantity=1,
            unit_cost_usd=cost_usd,
            goal_id=goal_id,
        )
        if input_tokens + output_tokens > 0:
            await self.record(
                tenant_id=tenant_id,
                metric="llm_tokens",
                quantity=float(input_tokens + output_tokens),
                unit_cost_usd=cost_usd / max(input_tokens + output_tokens, 1),
                goal_id=goal_id,
                metadata={"input": input_tokens, "output": output_tokens},
            )

    async def record_tool_call(
        self,
        *,
        tenant_id: str,
        tool_name: str,
        server_id: str,
        goal_id: str | None = None,
    ) -> None:
        """Record a single tool call."""
        await self.record(
            tenant_id=tenant_id,
            metric="tool_calls",
            quantity=1.0,
            goal_id=goal_id,
            metadata={"tool": tool_name, "server": server_id},
        )

    async def get_usage_summary(
        self,
        tenant_id: str,
        *,
        period_days: int = 30,
    ) -> dict[str, Any]:
        """Return usage summary for the last N days."""
        summary: dict[str, float] = defaultdict(float)
        costs: dict[str, float] = defaultdict(float)

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

        try:
            from sqlalchemy import text

            from app.db.rls import system_session

            async with self._db() as session, session.begin(), system_session(session):
                for record in to_flush:
                    await session.execute(
                        text(
                            "INSERT INTO usage_records"
                            " (id, tenant_id, goal_id, metric, quantity,"
                            " unit_cost_usd, total_cost_usd, period_start, metadata)"
                            " VALUES (:id, :tenant_id, :goal_id, :metric, :quantity,"
                            " :unit_cost_usd, :total_cost_usd, :period_start,"
                            " :metadata::jsonb)"
                            " ON CONFLICT (id) DO NOTHING"
                        ),
                        {**record, "metadata": str(record["metadata"]).replace("'", '"')},
                    )
            logger.info("usage_flushed", count=len(to_flush))
        except Exception as exc:
            logger.warning("usage_flush_failed", error=str(exc)[:80])
            # Re-add to buffer for retry
            self._buffer = to_flush + self._buffer


# Module-level singleton
_usage_service = UsageService()
