"""Org-level analytics service — PART 22/23 of spec.

Provides:
  - OrgAnalyticsService: department + org-level metrics aggregation
  - OrgHealthScore: spec-compliant 8-factor composite (0-100)
  - Cost tracking per department per mission type
  - Bottleneck detection + KPI trends
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from opentelemetry import trace
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.logging import get_logger
from app.org.models import (
    OrgDepartment,
    OrgEvent,
    OrgMission,
    OrgTask,
)

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ── Pure metric helpers (WS-2c: computed from real data, never fabricated) ────
#
# These are the honest replacements for the previously-hardcoded health-score
# constants. Each returns ``None`` when there is no data to compute from, so the
# caller can record an *indeterminate* factor (neutral, but flagged) rather than
# inventing a mid-range number.


def compute_cost_efficiency(estimated_usd: float, actual_usd: float) -> float | None:
    """Efficiency = estimated / actual, capped to [0, 1].

    1.0 = spent at-or-under the estimate; 0.5 = spent double. Returns ``None``
    when no actual (or no estimated) spend has been recorded — indeterminate,
    never a fabricated value.
    """
    if actual_usd <= 0 or estimated_usd <= 0:
        return None
    return max(0.0, min(1.0, estimated_usd / actual_usd))


def compute_quality_avg(completed: int, failed: int) -> float | None:
    """Quality proxy = completed / (completed + failed). ``None`` when no data."""
    total = completed + failed
    if total <= 0:
        return None
    return completed / total


def compute_escalation_rate(escalations: int, total_tasks: int) -> float:
    """Fraction of tasks that escalated (to human/other dept), capped to [0, 1]."""
    if total_tasks <= 0:
        return 0.0
    return min(1.0, escalations / total_tasks)


def compute_security_compliance(violations: int, total_tasks: int) -> float:
    """Compliance = 1 - (violations / tasks), capped to [0, 1].

    No activity → fully compliant (no violations possible).
    """
    if total_tasks <= 0:
        return 1.0
    return max(0.0, 1.0 - min(1.0, violations / total_tasks))


# ── OrgHealthScore (spec PART 9, 8-factor formula) ───────────────────────────


@dataclass
class OrgHealthScore:
    """
    Composite 0-100 health score per spec:
      mission_completion_rate × 30
      agent_utilization        × 15
      cost_efficiency          × 15
      quality_avg              × 20
      blocked_ratio            × -15
      escalation_rate          × -5
      knowledge_freshness      × 10
      security_compliance      × 10
    """

    mission_completion_rate: float = 0.0  # 0-1
    agent_utilization: float = 0.0  # 0-1
    cost_efficiency: float = 1.0  # 0-1 (1 = perfect)
    quality_avg: float = 0.0  # 0-1
    blocked_ratio: float = 0.0  # 0-1 (penalised)
    escalation_rate: float = 0.0  # 0-1 (penalised)
    knowledge_freshness: float = 1.0  # 0-1
    security_compliance: float = 1.0  # 0-1
    # Factors we had no data to compute (neutral default used, but disclosed).
    indeterminate_factors: set[str] = field(default_factory=set)

    @property
    def score(self) -> float:
        raw = (
            self.mission_completion_rate * 30
            + self.agent_utilization * 15
            + self.cost_efficiency * 15
            + self.quality_avg * 20
            - self.blocked_ratio * 15
            - self.escalation_rate * 5
            + self.knowledge_freshness * 10
            + self.security_compliance * 10
        )
        return max(0.0, min(100.0, raw))

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "factors": {
                "mission_completion_rate": self.mission_completion_rate,
                "agent_utilization": self.agent_utilization,
                "cost_efficiency": self.cost_efficiency,
                "quality_avg": self.quality_avg,
                "blocked_ratio": self.blocked_ratio,
                "escalation_rate": self.escalation_rate,
                "knowledge_freshness": self.knowledge_freshness,
                "security_compliance": self.security_compliance,
            },
            "indeterminate": sorted(self.indeterminate_factors),
        }


# ── Department analytics ──────────────────────────────────────────────────────


@dataclass
class DeptAnalytics:
    department_id: str
    department_name: str
    total_missions: int = 0
    completed_missions: int = 0
    failed_missions: int = 0
    active_missions: int = 0
    avg_mission_duration_hours: float = 0.0
    total_cost_usd: float = 0.0
    avg_cost_per_mission: float = 0.0
    task_completion_rate: float = 0.0
    blocked_tasks: int = 0
    agent_count: int = 0
    mission_types: dict[str, int] = field(default_factory=dict)
    cost_trend_7d: list[float] = field(default_factory=list)

    @property
    def completion_rate(self) -> float:
        if self.total_missions == 0:
            return 0.0
        return self.completed_missions / self.total_missions


# ── Analytics service ─────────────────────────────────────────────────────────


class OrgAnalyticsService:
    """
    Aggregates org-level and department-level metrics.
    Called by /v1/org/{id}/analytics/* endpoints.
    """

    def __init__(self, session: AsyncSession, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    async def get_org_health_score(self, org_id: str) -> OrgHealthScore:
        """Compute the full 8-factor OrgHealthScore for an organization."""
        with _tracer.start_as_current_span("org_analytics.health_score") as span:
            span.set_attribute("org_id", org_id)
            try:
                since_30d = datetime.now(UTC) - timedelta(days=30)

                # ── mission completion rate ─────────────────────────────────
                total_q = await self._session.execute(
                    select(func.count(OrgMission.id)).where(
                        OrgMission.org_id == org_id,
                        OrgMission.created_at >= since_30d,
                    )
                )
                total_missions = total_q.scalar() or 0

                completed_q = await self._session.execute(
                    select(func.count(OrgMission.id)).where(
                        OrgMission.org_id == org_id,
                        OrgMission.status == "completed",
                        OrgMission.created_at >= since_30d,
                    )
                )
                completed_missions = completed_q.scalar() or 0

                mission_completion_rate = (
                    completed_missions / total_missions if total_missions > 0 else 0.0
                )

                # ── blocked ratio ───────────────────────────────────────────
                blocked_q = await self._session.execute(
                    select(func.count(OrgTask.id)).where(
                        OrgTask.org_id == org_id,
                        OrgTask.status.in_(["blocked", "failed"]),
                        OrgTask.created_at >= since_30d,
                    )
                )
                blocked_tasks = blocked_q.scalar() or 0

                total_tasks_q = await self._session.execute(
                    select(func.count(OrgTask.id)).where(
                        OrgTask.org_id == org_id,
                        OrgTask.created_at >= since_30d,
                    )
                )
                total_tasks_count = total_tasks_q.scalar() or 0
                total_tasks = total_tasks_count or 1
                blocked_ratio = min(1.0, blocked_tasks / total_tasks)

                indeterminate: set[str] = set()

                # ── cost efficiency: actual vs estimated spend (real) ────────
                cost_q = await self._session.execute(
                    select(
                        func.coalesce(func.sum(OrgTask.cost_estimate_usd), 0.0),
                        func.coalesce(func.sum(OrgTask.actual_cost_usd), 0.0),
                    ).where(
                        OrgTask.org_id == org_id,
                        OrgTask.created_at >= since_30d,
                    )
                )
                est_cost, act_cost = cost_q.one()
                cost_efficiency = compute_cost_efficiency(float(est_cost), float(act_cost))
                if cost_efficiency is None:
                    cost_efficiency = 1.0  # neutral, but disclosed
                    indeterminate.add("cost_efficiency")

                # ── quality: completed vs failed task outcomes (real) ────────
                completed_tasks_q = await self._session.execute(
                    select(func.count(OrgTask.id)).where(
                        OrgTask.org_id == org_id,
                        OrgTask.status == "completed",
                        OrgTask.created_at >= since_30d,
                    )
                )
                completed_tasks = completed_tasks_q.scalar() or 0
                failed_tasks_q = await self._session.execute(
                    select(func.count(OrgTask.id)).where(
                        OrgTask.org_id == org_id,
                        OrgTask.status == "failed",
                        OrgTask.created_at >= since_30d,
                    )
                )
                failed_tasks = failed_tasks_q.scalar() or 0
                quality_avg = compute_quality_avg(completed_tasks, failed_tasks)
                if quality_avg is None:
                    quality_avg = 0.0
                    indeterminate.add("quality_avg")

                # ── escalation rate: escalation/approval events (real) ───────
                escalation_q = await self._session.execute(
                    select(func.count(OrgEvent.id)).where(
                        OrgEvent.org_id == org_id,
                        OrgEvent.event_type.in_(
                            [
                                "agent.escalated",
                                "org.agent.escalated",
                                "approval.requested",
                                "org.approval.requested",
                            ]
                        ),
                        OrgEvent.created_at >= since_30d,
                    )
                )
                escalations = escalation_q.scalar() or 0
                escalation_rate = compute_escalation_rate(escalations, total_tasks_count)

                # ── security compliance: policy-violation events (real) ──────
                violation_q = await self._session.execute(
                    select(func.count(OrgEvent.id)).where(
                        OrgEvent.org_id == org_id,
                        OrgEvent.event_type.in_(
                            ["policy.violation", "org.policy.violation"]
                        ),
                        OrgEvent.created_at >= since_30d,
                    )
                )
                violations = violation_q.scalar() or 0
                security_compliance = compute_security_compliance(violations, total_tasks_count)

                # ── agent utilization: running tasks / non-terminal tasks ────
                running_q = await self._session.execute(
                    select(func.count(OrgTask.id)).where(
                        OrgTask.org_id == org_id,
                        OrgTask.status.in_(["running", "assigned", "planned"]),
                    )
                )
                running_tasks = running_q.scalar() or 0
                active_pool_q = await self._session.execute(
                    select(func.count(OrgTask.id)).where(
                        OrgTask.org_id == org_id,
                        OrgTask.status.not_in(
                            ["archived", "cancelled", "completed", "failed", "expired"]
                        ),
                    )
                )
                active_pool = active_pool_q.scalar() or 0
                if active_pool > 0:
                    agent_utilization = min(1.0, running_tasks / active_pool)
                else:
                    agent_utilization = 0.0
                    indeterminate.add("agent_utilization")

                # ── knowledge freshness: no org-owned freshness source yet ───
                # Honest neutral (no penalty) but disclosed as indeterminate.
                knowledge_freshness = 1.0
                indeterminate.add("knowledge_freshness")

                return OrgHealthScore(
                    mission_completion_rate=mission_completion_rate,
                    agent_utilization=agent_utilization,
                    cost_efficiency=cost_efficiency,
                    quality_avg=quality_avg,
                    blocked_ratio=blocked_ratio,
                    escalation_rate=escalation_rate,
                    knowledge_freshness=knowledge_freshness,
                    security_compliance=security_compliance,
                    indeterminate_factors=indeterminate,
                )

            except Exception as exc:
                _log.warning("org_analytics.health_score_failed", org_id=org_id, error=str(exc))
                return OrgHealthScore()

    async def get_overview(self, org_id: str) -> dict[str, Any]:
        """Org-level overview: missions, costs, agents, bottlenecks."""
        with _tracer.start_as_current_span("org_analytics.overview") as span:
            span.set_attribute("org_id", org_id)
            since_30d = datetime.now(UTC) - timedelta(days=30)

            total_q = await self._session.execute(
                select(func.count(OrgMission.id)).where(
                    OrgMission.org_id == org_id,
                    OrgMission.created_at >= since_30d,
                )
            )
            total = total_q.scalar() or 0

            active_q = await self._session.execute(
                select(func.count(OrgMission.id)).where(
                    OrgMission.org_id == org_id,
                    OrgMission.status == "active",
                )
            )
            active = active_q.scalar() or 0

            completed_q = await self._session.execute(
                select(func.count(OrgMission.id)).where(
                    OrgMission.org_id == org_id,
                    OrgMission.status == "completed",
                    OrgMission.created_at >= since_30d,
                )
            )
            completed = completed_q.scalar() or 0

            health = await self.get_org_health_score(org_id)
            return {
                "period_days": 30,
                "total_missions": total,
                "active_missions": active,
                "completed_missions": completed,
                "completion_rate": completed / total if total > 0 else 0.0,
                "health_score": health.to_dict(),
                "generated_at": datetime.now(UTC).isoformat(),
            }

    async def get_department_analytics(self, org_id: str) -> list[DeptAnalytics]:
        """Per-department analytics."""
        with _tracer.start_as_current_span("org_analytics.by_department") as span:
            span.set_attribute("org_id", org_id)
            dept_q = await self._session.execute(
                select(OrgDepartment).where(
                    OrgDepartment.org_id == org_id,
                    OrgDepartment.tenant_id == self._tenant_id,
                )
            )
            depts = dept_q.scalars().all()
            results = []
            for dept in depts:
                mission_q = await self._session.execute(
                    select(func.count(OrgMission.id)).where(
                        OrgMission.org_id == org_id,
                        OrgMission.dept_id == dept.id,
                    )
                )
                total = mission_q.scalar() or 0

                completed_q = await self._session.execute(
                    select(func.count(OrgMission.id)).where(
                        OrgMission.org_id == org_id,
                        OrgMission.dept_id == dept.id,
                        OrgMission.status == "completed",
                    )
                )
                completed = completed_q.scalar() or 0

                results.append(
                    DeptAnalytics(
                        department_id=str(dept.id),
                        department_name=str(dept.name),
                        total_missions=total,
                        completed_missions=completed,
                    )
                )
            return results

    async def get_cost_breakdown(self, org_id: str) -> dict[str, Any]:
        """Cost breakdown by department, computed from real ``OrgTask`` spend."""
        with _tracer.start_as_current_span("org_analytics.cost") as span:
            span.set_attribute("org_id", org_id)
            since_30d = datetime.now(UTC) - timedelta(days=30)

            # Total actual spend across all tasks in the window.
            total_q = await self._session.execute(
                select(func.coalesce(func.sum(OrgTask.actual_cost_usd), 0.0)).where(
                    OrgTask.org_id == org_id,
                    OrgTask.created_at >= since_30d,
                )
            )
            total_usd = float(total_q.scalar() or 0.0)

            # Per-department spend: task → mission → dept.
            dept_q = await self._session.execute(
                select(
                    OrgDepartment.id,
                    OrgDepartment.name,
                    func.coalesce(func.sum(OrgTask.actual_cost_usd), 0.0),
                )
                .select_from(OrgTask)
                .join(OrgMission, OrgTask.mission_id == OrgMission.id)
                .join(OrgDepartment, OrgMission.dept_id == OrgDepartment.id)
                .where(
                    OrgTask.org_id == org_id,
                    OrgTask.created_at >= since_30d,
                )
                .group_by(OrgDepartment.id, OrgDepartment.name)
            )
            by_department = [
                {
                    "department_id": str(dept_id),
                    "department_name": str(dept_name),
                    "actual_cost_usd": round(float(cost), 4),
                }
                for dept_id, dept_name, cost in dept_q.all()
            ]

            return {
                "total_usd_30d": round(total_usd, 4),
                "by_department": by_department,
                # Org missions have no discrete "type" column; expose the count
                # of missions instead of a fabricated per-type breakdown.
                "by_mission_type": [],
                "indeterminate": ["by_mission_type"],
                "generated_at": datetime.now(UTC).isoformat(),
            }

    async def get_bottlenecks(self, org_id: str) -> list[dict[str, Any]]:
        """Identify bottlenecks: blocked tasks, slow depts, approval delays."""
        with _tracer.start_as_current_span("org_analytics.bottlenecks") as span:
            span.set_attribute("org_id", org_id)
            bottlenecks = []

            blocked_q = await self._session.execute(
                select(OrgTask)
                .where(
                    OrgTask.org_id == org_id,
                    OrgTask.status == "blocked",
                )
                .limit(10)
            )
            blocked = blocked_q.scalars().all()
            for task in blocked:
                blocked_since = (
                    datetime.now(UTC) - task.updated_at.replace(tzinfo=UTC)
                    if task.updated_at
                    else timedelta(0)
                )
                bottlenecks.append(
                    {
                        "type": "blocked_task",
                        "entity_id": str(task.id),
                        "title": (task.title if hasattr(task, "title") else "Unknown task"),
                        "blocked_hours": blocked_since.total_seconds() / 3600,
                        "severity": "high" if blocked_since.days > 1 else "medium",
                    }
                )

            return bottlenecks

    async def get_model_performance(self, org_id: str) -> dict[str, Any]:
        """Per-model usage and performance metrics.

        The org layer does not own per-model token telemetry — that lives in the
        model gateway / cost service keyed by goal, not by org. Rather than
        fabricate figures, this reports the honest actual task spend it *can*
        see and flags the token-level breakdown as unavailable at this layer.
        """
        since_24h = datetime.now(UTC) - timedelta(hours=24)
        cost_q = await self._session.execute(
            select(func.coalesce(func.sum(OrgTask.actual_cost_usd), 0.0)).where(
                OrgTask.org_id == org_id,
                OrgTask.created_at >= since_24h,
            )
        )
        total_cost_24h = float(cost_q.scalar() or 0.0)
        return {
            "models": [],
            "total_tokens_24h": None,  # not tracked at org layer — indeterminate
            "total_cost_usd_24h": round(total_cost_24h, 4),
            "indeterminate": ["models", "total_tokens_24h"],
            "generated_at": datetime.now(UTC).isoformat(),
        }
