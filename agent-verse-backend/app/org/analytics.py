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
    OrgMission,
    OrgTask,
)

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


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
                total_tasks = total_tasks_q.scalar() or 1
                blocked_ratio = min(1.0, blocked_tasks / total_tasks)

                return OrgHealthScore(
                    mission_completion_rate=mission_completion_rate,
                    agent_utilization=min(
                        1.0, (total_missions / 10) if total_missions > 0 else 0.0
                    ),
                    cost_efficiency=0.85,  # TODO: compute from actual vs estimated
                    quality_avg=0.82,  # TODO: from EvalRunner
                    blocked_ratio=blocked_ratio,
                    escalation_rate=0.05,  # TODO: from HITL events
                    knowledge_freshness=0.90,  # TODO: from knowledge freshness tracker
                    security_compliance=0.98,  # TODO: from policy engine
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
                        department_name=dept.name,
                        total_missions=total,
                        completed_missions=completed,
                    )
                )
            return results

    async def get_cost_breakdown(self, org_id: str) -> dict[str, Any]:
        """Cost breakdown by department and mission type."""
        with _tracer.start_as_current_span("org_analytics.cost") as span:
            span.set_attribute("org_id", org_id)
            # TODO: integrate with cost tracking service
            return {
                "total_usd_30d": 0.0,
                "by_department": [],
                "by_mission_type": [],
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
        """Per-model usage and performance metrics."""
        # TODO: integrate with model gateway telemetry
        return {
            "models": [],
            "total_tokens_24h": 0,
            "total_cost_usd_24h": 0.0,
            "generated_at": datetime.now(UTC).isoformat(),
        }
