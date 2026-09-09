"""GoalAnalyticsAggregator — computes behavioural metrics from goal event history."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class GoalMetrics:
    total: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    success_rate: float = 0.0
    avg_duration_s: float = 0.0
    avg_cost_usd: float = 0.0
    total_cost_usd: float = 0.0


@dataclass
class ToolMetrics:
    tool_name: str
    call_count: int = 0
    failure_count: int = 0
    avg_latency_ms: float = 0.0
    failure_rate: float = 0.0


@dataclass
class AgentMetrics:
    agent_id: str
    goal_count: int = 0
    success_rate: float = 0.0
    avg_eval_score: float = 0.0
    avg_cost_usd: float = 0.0


def _goal_status_completed(status: Any) -> bool:
    """Check if a goal status represents completion (tolerates StrEnum variants)."""
    s = str(status).lower()
    return s in ("complete", "completed")


def _goal_status_failed(status: Any) -> bool:
    s = str(status).lower()
    return s == "failed"


def _goal_status_cancelled(status: Any) -> bool:
    s = str(status).lower()
    return s == "cancelled"


class GoalAnalyticsAggregator:
    """Computes analytics from the GoalService in-memory goal states.

    When ``db`` is provided, ``goal_metrics()`` will query PostgreSQL for
    accurate analytics instead of reading from the in-process in-memory store.
    """

    def __init__(self, goal_service: Any = None, db: Any = None) -> None:
        self._goal_service = goal_service
        self._db = db

    async def _get_goals_from_db(self, tenant_id: str, db: Any, days: int = 30) -> list[dict]:
        """Query goals directly from PostgreSQL for accurate analytics."""
        if db is None:
            return []
        try:
            from sqlalchemy import text

            async with db() as session:
                result = await session.execute(
                    text("""
                        SELECT id, status, priority, agent_id, created_at, dry_run
                        FROM goals
                        WHERE tenant_id = :tid
                          AND created_at > NOW() - (:days * INTERVAL '1 day')
                        ORDER BY created_at DESC
                        LIMIT 10000
                    """),
                    {"tid": tenant_id, "days": days},
                )
                rows = result.fetchall()
            return [
                {
                    "id": r[0],
                    "status": r[1],
                    "priority": r[2],
                    "agent_id": r[3],
                    "created_at": r[4].isoformat() if r[4] else "",
                    "dry_run": r[5],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("analytics_db_query_failed", error=str(exc))
            return []

    @staticmethod
    def _parse_created_at(g: Any) -> datetime | None:
        """Return a timezone-aware datetime for goal.created_at regardless of type."""
        v = getattr(g, "created_at", None)
        if v is None:
            return None
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=UTC)
        if isinstance(v, str) and v:
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
            except (ValueError, AttributeError):
                return None
        return None

    def _get_all_goals(
        self,
        since: datetime | None = None,
        agent_id: str | None = None,
    ) -> list[Any]:
        """Get all goal states, optionally filtered."""
        goals = (
            list(self._goal_service._goals.values())
            if hasattr(self._goal_service, "_goals")
            else []
        )
        if since:
            # created_at may be a datetime or ISO string — normalise before comparing
            goals = [
                g for g in goals if (ts := self._parse_created_at(g)) is not None and ts >= since
            ]
        if agent_id:
            goals = [g for g in goals if getattr(g, "agent_id", None) == agent_id]
        return goals

    async def goal_metrics(
        self,
        tenant_id: str = "",
        days: int = 30,
        agent_id: str | None = None,
    ) -> GoalMetrics:
        """Compute success/failure breakdown for goals.

        Uses PostgreSQL when ``tenant_id`` and ``db`` are available;
        falls back to the in-process in-memory store otherwise.
        """
        since = datetime.now(UTC) - timedelta(days=days)
        goals: list[Any]

        if tenant_id and self._db is not None:
            db_rows = await self._get_goals_from_db(tenant_id, self._db, days)
            if db_rows:
                m = GoalMetrics(total=len(db_rows))
                for row in db_rows:
                    status = row.get("status", "")
                    if _goal_status_completed(status):
                        m.completed += 1
                    elif _goal_status_failed(status):
                        m.failed += 1
                    elif _goal_status_cancelled(status):
                        m.cancelled += 1
                m.success_rate = round(m.completed / m.total, 4) if m.total > 0 else 0.0
                return m

        goals = self._get_all_goals(since=since, agent_id=agent_id)

        m = GoalMetrics(total=len(goals))
        durations: list[float] = []
        costs: list[float] = []

        for g in goals:
            status = getattr(g, "status", None)
            if _goal_status_completed(status):
                m.completed += 1
            elif _goal_status_failed(status):
                m.failed += 1
            elif _goal_status_cancelled(status):
                m.cancelled += 1

            cost = getattr(g, "cost_usd", 0.0) or 0.0
            costs.append(cost)
            m.total_cost_usd += cost

            if hasattr(g, "created_at") and hasattr(g, "completed_at") and g.completed_at:
                duration = (g.completed_at - g.created_at).total_seconds()
                durations.append(duration)

        m.success_rate = round(m.completed / m.total, 4) if m.total > 0 else 0.0
        m.avg_duration_s = round(statistics.mean(durations), 2) if durations else 0.0
        m.avg_cost_usd = round(statistics.mean(costs), 6) if costs else 0.0
        return m

    def tool_metrics(self, days: int = 30) -> list[ToolMetrics]:
        """Compute tool usage and reliability from goal events."""
        since = datetime.now(UTC) - timedelta(days=days)
        goals = self._get_all_goals(since=since)

        tool_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for g in goals:
            for evt in getattr(g, "events", []):
                if evt.get("type") in ("tool_call_complete", "tool_call_failed", "step_complete"):
                    tool_name = evt.get("tool", evt.get("tool_name", "unknown"))
                    tool_calls[tool_name].append(evt)

        results: list[ToolMetrics] = []
        for tool_name, calls in tool_calls.items():
            failures = sum(1 for c in calls if c.get("error") or c.get("status") == "failed")
            latencies = [c.get("latency_ms", 0.0) for c in calls if c.get("latency_ms")]
            m = ToolMetrics(
                tool_name=tool_name,
                call_count=len(calls),
                failure_count=failures,
                avg_latency_ms=round(statistics.mean(latencies), 2) if latencies else 0.0,
                failure_rate=round(failures / len(calls), 4) if calls else 0.0,
            )
            results.append(m)
        return sorted(results, key=lambda x: x.call_count, reverse=True)

    async def tool_metrics_db(self, tenant_id: str, days: int = 30) -> list[ToolMetrics]:
        """Query tool call metrics from goal_events table in PostgreSQL.

        Falls back to in-memory tool_metrics() when DB is unavailable.
        """
        if self._db is None or not tenant_id:
            return self.tool_metrics(days=days)
        try:
            from sqlalchemy import text

            async with self._db() as session:
                result = await session.execute(
                    text("""
                        SELECT
                            e.payload->>'tool_name' AS tool_name,
                            COUNT(*) AS call_count,
                            SUM(CASE WHEN e.payload->>'status' = 'failed'
                                          OR e.payload->>'error' IS NOT NULL
                                     THEN 1 ELSE 0 END) AS failure_count,
                            AVG((e.payload->>'latency_ms')::numeric) AS avg_latency_ms
                        FROM goal_events e
                        JOIN goals g ON g.id = e.goal_id
                        WHERE g.tenant_id = :tid
                          AND e.event_type IN
                              ('tool_call_complete', 'tool_call_failed', 'step_complete')
                          AND e.created_at > NOW() - (:days * INTERVAL '1 day')
                          AND e.payload->>'tool_name' IS NOT NULL
                        GROUP BY e.payload->>'tool_name'
                        ORDER BY call_count DESC
                        LIMIT 100
                    """),
                    {"tid": tenant_id, "days": days},
                )
                rows = result.fetchall()

            results: list[ToolMetrics] = []
            for row in rows:
                tool_name = row[0] or "unknown"
                call_count = int(row[1] or 0)
                failure_count = int(row[2] or 0)
                avg_latency = float(row[3] or 0.0)
                results.append(
                    ToolMetrics(
                        tool_name=tool_name,
                        call_count=call_count,
                        failure_count=failure_count,
                        avg_latency_ms=round(avg_latency, 2),
                        failure_rate=round(failure_count / max(call_count, 1), 4),
                    )
                )
            if results:
                return results
        except Exception as exc:
            logger.warning("tool_metrics_db_failed", error=str(exc))
        # Fallback to in-memory
        return self.tool_metrics(days=days)

    def cost_trends(self, days: int = 30, bucket: str = "day") -> list[dict[str, Any]]:
        """Return daily/weekly cost aggregates."""
        since = datetime.now(UTC) - timedelta(days=days)
        goals = self._get_all_goals(since=since)

        buckets: dict[str, float] = defaultdict(float)
        for g in goals:
            created = getattr(g, "created_at", None)
            cost = getattr(g, "cost_usd", 0.0) or 0.0
            if created:
                # created may be str or datetime — normalise to datetime
                created_dt = self._parse_created_at(g)
                if created_dt is None:
                    continue
                key = (
                    created_dt.strftime("%Y-%m-%d")
                    if bucket == "day"
                    else created_dt.strftime("%Y-W%V")
                )
                buckets[key] += cost

        return [{"period": k, "cost_usd": round(v, 6)} for k, v in sorted(buckets.items())]

    async def cost_trends_db(
        self, tenant_id: str, days: int = 30, bucket: str = "day"
    ) -> list[dict[str, Any]]:
        """Query cost trends from goals.cost_usd via DATE_TRUNC in PostgreSQL.

        Returns list of {period, cost_usd} dicts, falling back to in-memory
        cost_trends() when DB is unavailable.
        """
        if self._db is None or not tenant_id:
            return self.cost_trends(days=days, bucket=bucket)
        try:
            from sqlalchemy import text

            trunc = "day" if bucket == "day" else "week"
            async with self._db() as session:
                result = await session.execute(
                    text(f"""
                        SELECT
                            DATE_TRUNC('{trunc}', created_at)::date AS period,
                            SUM(COALESCE(cost_usd, 0)) AS cost_usd
                        FROM goals
                        WHERE tenant_id = :tid
                          AND created_at > NOW() - (:days * INTERVAL '1 day')
                        GROUP BY period
                        ORDER BY period ASC
                    """),
                    {"tid": tenant_id, "days": days},
                )
                rows = result.fetchall()

            db_results = [
                {"period": str(row[0]), "cost_usd": round(float(row[1] or 0), 6)} for row in rows
            ]
            if db_results:
                return db_results
        except Exception as exc:
            logger.warning("cost_trends_db_failed", error=str(exc))
        # Fallback to in-memory
        return self.cost_trends(days=days, bucket=bucket)

    async def cost_by_model_db(self, tenant_id: str, days: int = 30) -> dict[str, float]:
        """Return cost aggregated by model from cost_ledger table."""
        if self._db is None or not tenant_id:
            return {}
        try:
            from sqlalchemy import text

            async with self._db() as session:
                result = await session.execute(
                    text("""
                        SELECT
                            COALESCE(tool_name, 'unknown') AS model,
                            SUM(cost_usd) AS total_cost
                        FROM cost_ledger
                        WHERE tenant_id = :tid
                          AND created_at > NOW() - (:days * INTERVAL '1 day')
                        GROUP BY model
                        ORDER BY total_cost DESC
                        LIMIT 20
                    """),
                    {"tid": tenant_id, "days": days},
                )
                rows = result.fetchall()
            return {str(row[0]): round(float(row[1] or 0), 6) for row in rows}
        except Exception as exc:
            logger.warning("cost_by_model_db_failed", error=str(exc))
        return {}

    def agent_metrics(self, days: int = 30) -> list[AgentMetrics]:
        """Per-agent goal performance."""
        since = datetime.now(UTC) - timedelta(days=days)
        goals = self._get_all_goals(since=since)

        by_agent: dict[str, list[Any]] = defaultdict(list)
        for g in goals:
            agent_id = getattr(g, "agent_id", "default") or "default"
            by_agent[agent_id].append(g)

        results: list[AgentMetrics] = []
        for agent_id, agent_goals in by_agent.items():
            completed = [
                g for g in agent_goals if _goal_status_completed(getattr(g, "status", None))
            ]
            costs = [getattr(g, "cost_usd", 0.0) or 0.0 for g in agent_goals]
            eval_scores: list[float] = [
                float(score)
                for g in agent_goals
                if (score := getattr(g, "eval_score", None)) is not None
            ]

            results.append(
                AgentMetrics(
                    agent_id=agent_id,
                    goal_count=len(agent_goals),
                    success_rate=round(len(completed) / len(agent_goals), 4)
                    if agent_goals
                    else 0.0,
                    avg_eval_score=round(statistics.mean(eval_scores), 4) if eval_scores else 0.0,
                    avg_cost_usd=round(statistics.mean(costs), 6) if costs else 0.0,
                )
            )
        return sorted(results, key=lambda x: x.goal_count, reverse=True)
