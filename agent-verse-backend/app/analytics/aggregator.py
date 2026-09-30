"""GoalAnalyticsAggregator — computes behavioural metrics from goal event history."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger

logger = get_logger(__name__)


class AnalyticsUnavailableError(RuntimeError):
    """The tenant's analytics could not be read (the API answers 503).

    Raised instead of falling back to another source: an empty or failed DB
    read must never be papered over with in-memory goals of other tenants.
    """


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


# Tool-call events as the executor emits them (EventStore stores the emitted
# dict as the goal_events payload): the tool is under "tool" — older rows used
# "tool_name" — and a successful call carries ``"success": true, "error": ""``.
_TOOL_EVENT_TYPES = ("tool_call_complete", "tool_call_failed")


def _event_tool_name(evt: dict[str, Any]) -> str | None:
    name = evt.get("tool") or evt.get("tool_name")
    return str(name) if name else None


def _tool_event_failed(evt: dict[str, Any]) -> bool:
    return (
        evt.get("type") == "tool_call_failed"
        or evt.get("success") is False
        or evt.get("status") == "failed"
        or bool(evt.get("error"))
    )


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
    """Computes a tenant's goal analytics.

    With ``db`` the database is the ONLY source: an empty result means the
    tenant has no data (zeros / empty lists) and a failed query raises
    :class:`AnalyticsUnavailableError`. The in-process GoalService goals are
    read only when there is no database at all, and then filtered to the
    tenant. A falsy ``tenant_id`` yields no data — never every tenant's.
    """

    def __init__(self, goal_service: Any = None, db: Any = None) -> None:
        self._goal_service = goal_service
        self._db = db

    async def _get_goals_from_db(
        self, tenant_id: str, db: Any, days: int = 30, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        """The tenant's goals (optionally one agent's) with duration inputs and
        their ledger cost. Raises :class:`AnalyticsUnavailableError` on failure."""
        if db is None or not tenant_id:
            return []
        params: dict[str, Any] = {"tid": tenant_id, "days": days}
        agent_clause = ""
        if agent_id:
            agent_clause = "AND g.agent_id = :agent_id"
            params["agent_id"] = agent_id
        try:
            from sqlalchemy import text

            async with (
                db() as session,
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    text(f"""
                        SELECT g.id, g.status, g.priority, g.agent_id, g.created_at,
                               g.dry_run, g.completed_at, COALESCE(c.goal_cost, 0)
                        FROM goals g
                        LEFT JOIN (
                            SELECT goal_id, SUM(COALESCE(cost_usd, 0)) AS goal_cost
                            FROM cost_ledger
                            WHERE tenant_id = :tid
                              AND created_at > NOW() - (:days * INTERVAL '1 day')
                            GROUP BY goal_id
                        ) c ON c.goal_id = g.id
                        WHERE g.tenant_id = :tid
                          AND g.created_at > NOW() - (:days * INTERVAL '1 day')
                          {agent_clause}
                        ORDER BY g.created_at DESC
                        LIMIT 10000
                    """),
                    params,
                )
                rows = result.fetchall()
        except Exception as exc:
            logger.warning("analytics_db_query_failed", error=str(exc))
            raise AnalyticsUnavailableError("goal analytics query failed") from exc
        return [
            {
                "id": r[0],
                "status": r[1],
                "priority": r[2],
                "agent_id": r[3],
                "created_at": r[4],
                "dry_run": r[5],
                "completed_at": r[6],
                "cost_usd": float(r[7] or 0.0),
            }
            for r in rows
        ]

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
        *,
        tenant_id: str,
    ) -> list[Any]:
        """The tenant's in-process goal states, optionally filtered.

        GoalService holds every tenant's goals, so the tenant filter is
        mandatory; no tenant means no goals.
        """
        if not tenant_id:
            return []
        goals = (
            list(self._goal_service._goals.values())
            if hasattr(self._goal_service, "_goals")
            else []
        )
        goals = [g for g in goals if getattr(g, "tenant_id", None) == tenant_id]
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
        """Success/failure breakdown, duration and cost of the tenant's goals.

        PostgreSQL is authoritative when configured (empty = zeros, error =
        :class:`AnalyticsUnavailableError`); the tenant-filtered in-memory
        store is used only without a database.
        """
        if not tenant_id:
            return GoalMetrics()
        if self._db is not None:
            rows = await self._get_goals_from_db(tenant_id, self._db, days, agent_id)
            return self._metrics_from(
                [
                    (
                        r["status"],
                        r["cost_usd"],
                        self._duration_s(r["created_at"], r["completed_at"]),
                    )
                    for r in rows
                ]
            )

        since = datetime.now(UTC) - timedelta(days=days)
        goals = self._get_all_goals(since=since, agent_id=agent_id, tenant_id=tenant_id)
        return self._metrics_from(
            [
                (
                    getattr(g, "status", None),
                    float(getattr(g, "cost_usd", 0.0) or 0.0),
                    self._duration_s(
                        self._parse_created_at(g), getattr(g, "completed_at", None)
                    ),
                )
                for g in goals
            ]
        )

    @staticmethod
    def _as_aware(v: Any) -> datetime | None:
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=UTC)
        if isinstance(v, str) and v:
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        return None

    @classmethod
    def _duration_s(cls, created: Any, completed: Any) -> float | None:
        start, end = cls._as_aware(created), cls._as_aware(completed)
        if start is None or end is None:
            return None
        return (end - start).total_seconds()

    @staticmethod
    def _metrics_from(goals: list[tuple[Any, float, float | None]]) -> GoalMetrics:
        """Aggregate ``(status, cost_usd, duration_s | None)`` tuples."""
        m = GoalMetrics(total=len(goals))
        durations: list[float] = []
        costs: list[float] = []
        for status, cost, duration in goals:
            if _goal_status_completed(status):
                m.completed += 1
            elif _goal_status_failed(status):
                m.failed += 1
            elif _goal_status_cancelled(status):
                m.cancelled += 1
            costs.append(cost)
            m.total_cost_usd += cost
            if duration is not None:
                durations.append(duration)
        m.total_cost_usd = round(m.total_cost_usd, 6)
        m.success_rate = round(m.completed / m.total, 4) if m.total > 0 else 0.0
        m.avg_duration_s = round(statistics.mean(durations), 2) if durations else 0.0
        m.avg_cost_usd = round(statistics.mean(costs), 6) if costs else 0.0
        return m

    def tool_metrics(self, days: int = 30, *, tenant_id: str) -> list[ToolMetrics]:
        """Tool usage and reliability from the tenant's in-memory goal events."""
        since = datetime.now(UTC) - timedelta(days=days)
        goals = self._get_all_goals(since=since, tenant_id=tenant_id)

        tool_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for g in goals:
            for evt in getattr(g, "events", []):
                if evt.get("type") in _TOOL_EVENT_TYPES and (name := _event_tool_name(evt)):
                    tool_calls[name].append(evt)

        results: list[ToolMetrics] = []
        for tool_name, calls in tool_calls.items():
            failures = sum(1 for c in calls if _tool_event_failed(c))
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

        Reads the executor's event shape: the tool under ``tool`` (``tool_name``
        on older rows); a call failed when it is a ``tool_call_failed`` event or
        carries ``success: false``, ``status: failed`` or a non-empty ``error``.
        It used to read only ``tool_name`` — which the executor never writes —
        and counted every ``"error": ""`` success as a failure.

        Empty result = no tool calls; a failed query raises
        :class:`AnalyticsUnavailableError`. The tenant-filtered in-memory
        :meth:`tool_metrics` is used only when no database is configured.
        """
        if not tenant_id:
            return []
        if self._db is None:
            return self.tool_metrics(days=days, tenant_id=tenant_id)
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    text("""
                        SELECT
                            COALESCE(NULLIF(e.payload->>'tool', ''),
                                     NULLIF(e.payload->>'tool_name', '')) AS tool_name,
                            COUNT(*) AS call_count,
                            SUM(CASE WHEN e.event_type = 'tool_call_failed'
                                          OR e.payload->>'success' = 'false'
                                          OR e.payload->>'status' = 'failed'
                                          OR COALESCE(e.payload->>'error', '') <> ''
                                     THEN 1 ELSE 0 END) AS failure_count,
                            AVG(NULLIF(e.payload->>'latency_ms', '')::numeric) AS avg_latency_ms
                        FROM goal_events e
                        JOIN goals g ON g.id = e.goal_id
                        WHERE g.tenant_id = :tid
                          AND e.tenant_id = :tid
                          AND e.event_type IN ('tool_call_complete', 'tool_call_failed')
                          AND e.created_at > NOW() - (:days * INTERVAL '1 day')
                          AND COALESCE(NULLIF(e.payload->>'tool', ''),
                                       NULLIF(e.payload->>'tool_name', '')) IS NOT NULL
                        GROUP BY 1
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
        except Exception as exc:
            logger.warning("tool_metrics_db_failed", error=str(exc))
            raise AnalyticsUnavailableError("tool analytics query failed") from exc
        return results

    def cost_trends(
        self, days: int = 30, bucket: str = "day", *, tenant_id: str
    ) -> list[dict[str, Any]]:
        """Daily/weekly cost aggregates of the tenant's in-memory goals."""
        since = datetime.now(UTC) - timedelta(days=days)
        goals = self._get_all_goals(since=since, tenant_id=tenant_id)

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
        """Query cost trends from the cost_ledger via DATE_TRUNC in PostgreSQL.

        Costs live in ``cost_ledger`` (per-tool spend rows), not on ``goals`` —
        the goals table has no cost column. Returns list of {period, cost_usd}
        dicts; empty = no spend, a failed query raises
        :class:`AnalyticsUnavailableError`. The tenant-filtered in-memory
        :meth:`cost_trends` is used only when no database is configured.
        """
        if not tenant_id:
            return []
        if self._db is None:
            return self.cost_trends(days=days, bucket=bucket, tenant_id=tenant_id)
        try:
            from sqlalchemy import text

            trunc = "day" if bucket == "day" else "week"
            async with (
                self._db() as session,
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    text(f"""
                        SELECT
                            DATE_TRUNC('{trunc}', created_at)::date AS period,
                            SUM(COALESCE(cost_usd, 0)) AS cost_usd
                        FROM cost_ledger
                        WHERE tenant_id = :tid
                          AND created_at > NOW() - (:days * INTERVAL '1 day')
                        GROUP BY period
                        ORDER BY period ASC
                    """),
                    {"tid": tenant_id, "days": days},
                )
                rows = result.fetchall()

        except Exception as exc:
            logger.warning("cost_trends_db_failed", error=str(exc))
            raise AnalyticsUnavailableError("cost analytics query failed") from exc
        return [{"period": str(row[0]), "cost_usd": round(float(row[1] or 0), 6)} for row in rows]

    async def cost_by_model_db(self, tenant_id: str, days: int = 30) -> dict[str, float]:
        """Return cost aggregated by model from cost_ledger table.

        Groups on ``cost_ledger.model``. It previously grouped on ``tool_name``,
        a column the partitioned ledger (migration 0058) does not have, so every
        call raised UndefinedColumn, was swallowed, and returned ``{}``. A failed
        query now raises :class:`AnalyticsUnavailableError`.
        """
        if self._db is None or not tenant_id:
            return {}
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    text("""
                        SELECT
                            COALESCE(model, 'unknown') AS model,
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
        except Exception as exc:
            logger.warning("cost_by_model_db_failed", error=str(exc))
            raise AnalyticsUnavailableError("cost-by-model query failed") from exc
        return {str(row[0]): round(float(row[1] or 0), 6) for row in rows}

    def agent_metrics(self, days: int = 30, *, tenant_id: str) -> list[AgentMetrics]:
        """Per-agent performance of the tenant's in-memory goals."""
        since = datetime.now(UTC) - timedelta(days=days)
        goals = self._get_all_goals(since=since, tenant_id=tenant_id)

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

    async def agent_metrics_db(self, tenant_id: str, days: int = 30) -> list[AgentMetrics]:
        """Per-agent goal performance via a single SQL GROUP BY.

        Aggregates ``goals`` (count + success rate) per agent in the database and
        LEFT JOINs a per-goal ``cost_ledger`` rollup for average cost, instead of
        pulling every goal into Python. Eval scores are not persisted as a goals
        column, so ``avg_eval_score`` is reported as 0.0 on the DB path. Empty =
        no goals; a failed query raises :class:`AnalyticsUnavailableError`. The
        tenant-filtered in-memory :meth:`agent_metrics` is used only when no
        database is configured.
        """
        if not tenant_id:
            return []
        if self._db is None:
            return self.agent_metrics(days=days, tenant_id=tenant_id)
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    text("""
                        SELECT
                            COALESCE(g.agent_id, 'default') AS agent_id,
                            COUNT(*) AS goal_count,
                            SUM(CASE WHEN lower(g.status) IN ('complete', 'completed')
                                     THEN 1 ELSE 0 END) AS completed,
                            COALESCE(SUM(c.goal_cost), 0) AS total_cost
                        FROM goals g
                        LEFT JOIN (
                            SELECT goal_id, SUM(COALESCE(cost_usd, 0)) AS goal_cost
                            FROM cost_ledger
                            WHERE tenant_id = :tid
                              AND created_at > NOW() - (:days * INTERVAL '1 day')
                            GROUP BY goal_id
                        ) c ON c.goal_id = g.id
                        WHERE g.tenant_id = :tid
                          AND g.created_at > NOW() - (:days * INTERVAL '1 day')
                        GROUP BY COALESCE(g.agent_id, 'default')
                        ORDER BY goal_count DESC
                        LIMIT 200
                    """),
                    {"tid": tenant_id, "days": days},
                )
                rows = result.fetchall()

            results: list[AgentMetrics] = []
            for row in rows:
                agent_id = str(row[0] or "default")
                goal_count = int(row[1] or 0)
                completed = int(row[2] or 0)
                total_cost = float(row[3] or 0.0)
                results.append(
                    AgentMetrics(
                        agent_id=agent_id,
                        goal_count=goal_count,
                        success_rate=round(completed / goal_count, 4) if goal_count else 0.0,
                        avg_eval_score=0.0,
                        avg_cost_usd=round(total_cost / goal_count, 6) if goal_count else 0.0,
                    )
                )
        except Exception as exc:
            logger.warning("agent_metrics_db_failed", error=str(exc))
            raise AnalyticsUnavailableError("agent analytics query failed") from exc
        return results
