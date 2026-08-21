"""'While You Were Away' Digest Generator.

Queries completed missions, pending approvals, blocked tasks, budget alerts,
and org decisions since the user's last visit, then synthesises a structured
digest with actionable items and insights.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from opentelemetry import trace
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.org.models import (
    OrgDecision,
    OrgDepartment,
    OrgEvent,
    OrgMission,
    OrgTask,
)

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

_DEFAULT_LOOKBACK_HOURS: int = 8
_CACHE_TTL_SECONDS: int = 300


# ─────────────────────────────────────────────────────────────────────────────
#  Domain objects
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DigestItem:
    category: str  # "completed" | "in_progress" | "needs_attention" | "insight"
    title: str
    summary: str
    icon: str  # emoji
    priority: int  # 1=highest
    action_required: bool
    action_type: str | None  # "approve" | "review" | "decide"
    related_id: str | None
    cost_usd: float | None
    duration_str: str | None  # "4h 23m"


@dataclass
class WhileYouWereAwayDigest:
    org_id: str
    tenant_id: str
    since: datetime
    generated_at: datetime

    completed_missions: list[DigestItem] = field(default_factory=list)
    completed_tasks: list[DigestItem] = field(default_factory=list)
    pending_approvals: list[DigestItem] = field(default_factory=list)
    blocked_items: list[DigestItem] = field(default_factory=list)
    budget_alerts: list[DigestItem] = field(default_factory=list)
    insights: list[DigestItem] = field(default_factory=list)

    total_cost_usd: float = 0.0
    missions_completed: int = 0
    missions_started: int = 0
    agents_active: int = 0
    decisions_made: int = 0

    summary_text: str = ""


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fmt_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m" if h else f"{m}m"


# ─────────────────────────────────────────────────────────────────────────────
#  Cache
# ─────────────────────────────────────────────────────────────────────────────


class DigestCache:
    """In-memory (+ optional Redis) cache with TTL."""

    def __init__(self, redis: Any | None = None) -> None:
        self._redis = redis
        self._local: dict[str, tuple[WhileYouWereAwayDigest, datetime]] = {}

    def _key(self, org_id: str, tenant_id: str) -> str:
        return f"digest:{tenant_id}:{org_id}"

    async def get(self, org_id: str, tenant_id: str) -> WhileYouWereAwayDigest | None:
        key = self._key(org_id, tenant_id)
        now = datetime.now(UTC)
        if key in self._local:
            digest, cached_at = self._local[key]
            if (now - cached_at).total_seconds() < _CACHE_TTL_SECONDS:
                return digest
            del self._local[key]
        return None

    async def set(self, org_id: str, tenant_id: str, digest: WhileYouWereAwayDigest) -> None:
        self._local[self._key(org_id, tenant_id)] = (digest, datetime.now(UTC))
        if self._redis is not None:
            try:
                marker = json.dumps({"generated_at": digest.generated_at.isoformat()})
                await self._redis.setex(self._key(org_id, tenant_id), _CACHE_TTL_SECONDS, marker)
            except Exception as exc:
                _log.warning("digest_cache.redis_set_failed", error=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
#  Generator
# ─────────────────────────────────────────────────────────────────────────────


class DigestGenerator:
    """Generates 'While You Were Away' digest by querying org events and state."""

    def __init__(self, session: AsyncSession, redis: Any | None = None) -> None:
        self._s = session
        self._cache = DigestCache(redis=redis)

    async def generate(
        self,
        org_id: str,
        tenant_id: str,
        since: datetime | None = None,
    ) -> WhileYouWereAwayDigest:
        with _tracer.start_as_current_span("digest.generate") as span:
            span.set_attribute("org_id", org_id)
            span.set_attribute("tenant_id", tenant_id)
            _log.info("digest.generate.start", org_id=org_id, tenant_id=tenant_id)

            if since is None:
                since = datetime.now(UTC) - timedelta(hours=_DEFAULT_LOOKBACK_HOURS)

            cached = await self._cache.get(org_id, tenant_id)
            if cached is not None:
                _log.info("digest.generate.cache_hit", org_id=org_id)
                return cached

            now = datetime.now(UTC)
            raw = await asyncio.gather(
                self._completed_missions(org_id, tenant_id, since),
                self._completed_tasks(org_id, tenant_id, since),
                self._pending_approvals(org_id, tenant_id),
                self._blocked_items(org_id, tenant_id),
                self._budget_alerts(org_id, tenant_id),
                self._insights(org_id, tenant_id, since),
                self._stats(org_id, tenant_id, since),
                return_exceptions=True,
            )

            def _safe(idx: int, default: Any) -> Any:
                r = raw[idx]
                if isinstance(r, BaseException):
                    _log.warning("digest.section_failed", idx=idx, error=str(r))
                    return default
                return r

            completed_missions: list[DigestItem] = _safe(0, [])
            completed_tasks: list[DigestItem] = _safe(1, [])
            pending_approvals: list[DigestItem] = _safe(2, [])
            blocked_items: list[DigestItem] = _safe(3, [])
            budget_alerts: list[DigestItem] = _safe(4, [])
            insights: list[DigestItem] = _safe(5, [])
            stats: dict[str, int] = _safe(6, {})

            total_cost = sum((i.cost_usd or 0.0) for i in completed_missions + completed_tasks)

            digest = WhileYouWereAwayDigest(
                org_id=org_id,
                tenant_id=tenant_id,
                since=since,
                generated_at=now,
                completed_missions=completed_missions,
                completed_tasks=completed_tasks,
                pending_approvals=pending_approvals,
                blocked_items=blocked_items,
                budget_alerts=budget_alerts,
                insights=insights,
                total_cost_usd=round(total_cost, 4),
                missions_completed=stats.get("missions_completed", 0),
                missions_started=stats.get("missions_started", 0),
                agents_active=stats.get("agents_active", 0),
                decisions_made=stats.get("decisions_made", 0),
            )
            digest.summary_text = _build_summary(digest)
            await self._cache.set(org_id, tenant_id, digest)

            span.set_attribute("missions_completed", digest.missions_completed)
            span.set_attribute("pending_approvals", len(digest.pending_approvals))
            _log.info(
                "digest.generate.done",
                org_id=org_id,
                missions_completed=digest.missions_completed,
                pending_approvals=len(digest.pending_approvals),
            )
            return digest

    # ── Section queries ────────────────────────────────────────────────────

    async def _completed_missions(
        self,
        org_id: str,
        tenant_id: str,
        since: datetime,
    ) -> list[DigestItem]:
        with _tracer.start_as_current_span("digest._completed_missions"):
            try:
                res = await self._s.execute(
                    select(OrgMission)
                    .where(
                        and_(
                            OrgMission.tenant_id == tenant_id,
                            OrgMission.org_id == org_id,
                            OrgMission.status == "completed",
                            OrgMission.updated_at >= since,
                        )
                    )
                    .order_by(OrgMission.updated_at.desc())
                    .limit(20)
                )
                items: list[DigestItem] = []
                for m in res.scalars().all():
                    raw_cost = getattr(m, "cost_usd", None) or getattr(m, "spent_usd", None)
                    started = getattr(m, "started_at", None)
                    duration = None
                    if started and m.updated_at:
                        duration = _fmt_duration((m.updated_at - started).total_seconds())
                    items.append(
                        DigestItem(
                            category="completed",
                            title=str(getattr(m, "title", "Mission")),
                            summary=f"Mission completed: {getattr(m, 'title', 'Mission')}",
                            icon="✅",
                            priority=2,
                            action_required=False,
                            action_type=None,
                            related_id=str(m.id),
                            cost_usd=float(raw_cost) if raw_cost is not None else None,
                            duration_str=duration,
                        )
                    )
                return items
            except Exception as exc:
                _log.warning("digest._completed_missions.failed", error=str(exc))
                return []

    async def _completed_tasks(
        self,
        org_id: str,
        tenant_id: str,
        since: datetime,
    ) -> list[DigestItem]:
        with _tracer.start_as_current_span("digest._completed_tasks"):
            try:
                res = await self._s.execute(
                    select(OrgTask)
                    .where(
                        and_(
                            OrgTask.tenant_id == tenant_id,
                            OrgTask.org_id == org_id,
                            OrgTask.status == "completed",
                            OrgTask.updated_at >= since,
                        )
                    )
                    .order_by(OrgTask.updated_at.desc())
                    .limit(50)
                )
                return [
                    DigestItem(
                        category="completed",
                        title=str(getattr(t, "title", "Task")),
                        summary=f"Task completed: {getattr(t, 'title', 'Task')}",
                        icon="✔️",
                        priority=3,
                        action_required=False,
                        action_type=None,
                        related_id=str(t.id),
                        cost_usd=None,
                        duration_str=None,
                    )
                    for t in res.scalars().all()
                ]
            except Exception as exc:
                _log.warning("digest._completed_tasks.failed", error=str(exc))
                return []

    async def _pending_approvals(self, org_id: str, tenant_id: str) -> list[DigestItem]:
        with _tracer.start_as_current_span("digest._pending_approvals"):
            try:
                res = await self._s.execute(
                    select(OrgEvent)
                    .where(
                        and_(
                            OrgEvent.tenant_id == tenant_id,
                            OrgEvent.org_id == org_id,
                            OrgEvent.event_type == "org.approval.requested",
                        )
                    )
                    .order_by(OrgEvent.created_at.desc())
                    .limit(20)
                )
                items: list[DigestItem] = []
                for ev in res.scalars().all():
                    payload: dict[str, Any] = getattr(ev, "payload", None) or {}
                    items.append(
                        DigestItem(
                            category="needs_attention",
                            title=payload.get("title", "Approval needed"),
                            summary=payload.get("description", "Action requires your approval"),
                            icon="⏳",
                            priority=1,
                            action_required=True,
                            action_type="approve",
                            related_id=payload.get("approval_id") or str(ev.id),
                            cost_usd=None,
                            duration_str=None,
                        )
                    )
                return items
            except Exception as exc:
                _log.warning("digest._pending_approvals.failed", error=str(exc))
                return []

    async def _blocked_items(self, org_id: str, tenant_id: str) -> list[DigestItem]:
        with _tracer.start_as_current_span("digest._blocked_items"):
            try:
                res = await self._s.execute(
                    select(OrgTask)
                    .where(
                        and_(
                            OrgTask.tenant_id == tenant_id,
                            OrgTask.org_id == org_id,
                            OrgTask.status == "blocked",
                        )
                    )
                    .order_by(OrgTask.updated_at.desc())
                    .limit(20)
                )
                return [
                    DigestItem(
                        category="needs_attention",
                        title=str(getattr(t, "title", "Task")),
                        summary=f"Blocked: {getattr(t, 'title', 'Task')}",
                        icon="🚫",
                        priority=1,
                        action_required=True,
                        action_type="review",
                        related_id=str(t.id),
                        cost_usd=None,
                        duration_str=None,
                    )
                    for t in res.scalars().all()
                ]
            except Exception as exc:
                _log.warning("digest._blocked_items.failed", error=str(exc))
                return []

    async def _budget_alerts(self, org_id: str, tenant_id: str) -> list[DigestItem]:
        with _tracer.start_as_current_span("digest._budget_alerts"):
            try:
                res = await self._s.execute(
                    select(OrgEvent)
                    .where(
                        and_(
                            OrgEvent.tenant_id == tenant_id,
                            OrgEvent.org_id == org_id,
                            OrgEvent.event_type.like("org.budget.%"),  # type: ignore[operator]
                        )
                    )
                    .order_by(OrgEvent.created_at.desc())
                    .limit(10)
                )
                items: list[DigestItem] = []
                for ev in res.scalars().all():
                    payload: dict[str, Any] = getattr(ev, "payload", None) or {}
                    items.append(
                        DigestItem(
                            category="needs_attention",
                            title=payload.get("title", "Budget alert"),
                            summary=payload.get("description", "Budget threshold reached"),
                            icon="💰",
                            priority=1,
                            action_required=True,
                            action_type="review",
                            related_id=str(ev.id),
                            cost_usd=(
                                float(payload["cost_usd"]) if "cost_usd" in payload else None
                            ),
                            duration_str=None,
                        )
                    )
                return items
            except Exception as exc:
                _log.warning("digest._budget_alerts.failed", error=str(exc))
                return []

    async def _insights(
        self,
        org_id: str,
        tenant_id: str,
        since: datetime,
    ) -> list[DigestItem]:
        with _tracer.start_as_current_span("digest._insights"):
            try:
                insights: list[DigestItem] = []

                # Bottleneck: 3+ tasks blocked in one dept
                rows = await self._s.execute(
                    select(
                        OrgTask.department_id,  # type: ignore[attr-defined]
                        func.count(OrgTask.id).label("cnt"),
                    )
                    .where(
                        and_(
                            OrgTask.tenant_id == tenant_id,
                            OrgTask.org_id == org_id,
                            OrgTask.status == "blocked",
                            OrgTask.department_id.is_not(None),  # type: ignore[union-attr]
                        )
                    )
                    .group_by(OrgTask.department_id)  # type: ignore[attr-defined]
                    .having(func.count(OrgTask.id) >= 3)
                )
                for row in rows.all():
                    dept_id = str(row[0])
                    cnt = int(row[1])
                    name_row = await self._s.execute(
                        select(OrgDepartment.name).where(
                            and_(
                                OrgDepartment.tenant_id == tenant_id,
                                OrgDepartment.id == dept_id,
                            )
                        )
                    )
                    dept_name = name_row.scalar_one_or_none() or f"Dept {dept_id[:8]}"
                    insights.append(
                        DigestItem(
                            category="insight",
                            title=f"Bottleneck in {dept_name}",
                            summary=f"{cnt} tasks blocked in {dept_name}. Review dependencies.",
                            icon="⚠️",
                            priority=2,
                            action_required=True,
                            action_type="review",
                            related_id=dept_id,
                            cost_usd=None,
                            duration_str=None,
                        )
                    )

                # High velocity
                cnt_row = await self._s.execute(
                    select(func.count(OrgMission.id)).where(
                        and_(
                            OrgMission.tenant_id == tenant_id,
                            OrgMission.org_id == org_id,
                            OrgMission.status == "completed",
                            OrgMission.updated_at >= since,
                        )
                    )
                )
                completed_count = cnt_row.scalar_one_or_none() or 0
                if completed_count >= 5:
                    insights.append(
                        DigestItem(
                            category="insight",
                            title="High productivity period",
                            summary=f"{completed_count} missions completed — exceptional performance.",  # noqa: E501
                            icon="🚀",
                            priority=3,
                            action_required=False,
                            action_type=None,
                            related_id=None,
                            cost_usd=None,
                            duration_str=None,
                        )
                    )

                return insights
            except Exception as exc:
                _log.warning("digest._insights.failed", error=str(exc))
                return []

    async def _stats(
        self,
        org_id: str,
        tenant_id: str,
        since: datetime,
    ) -> dict[str, int]:
        with _tracer.start_as_current_span("digest._stats"):
            try:
                c_res, s_res, d_res = await asyncio.gather(
                    self._s.execute(
                        select(func.count(OrgMission.id)).where(
                            and_(
                                OrgMission.tenant_id == tenant_id,
                                OrgMission.org_id == org_id,
                                OrgMission.status == "completed",
                                OrgMission.updated_at >= since,
                            )
                        )
                    ),
                    self._s.execute(
                        select(func.count(OrgMission.id)).where(
                            and_(
                                OrgMission.tenant_id == tenant_id,
                                OrgMission.org_id == org_id,
                                OrgMission.created_at >= since,
                            )
                        )
                    ),
                    self._s.execute(
                        select(func.count(OrgDecision.id)).where(
                            and_(
                                OrgDecision.tenant_id == tenant_id,
                                OrgDecision.org_id == org_id,
                                OrgDecision.created_at >= since,
                            )
                        )
                    ),
                )
                return {
                    "missions_completed": c_res.scalar_one_or_none() or 0,
                    "missions_started": s_res.scalar_one_or_none() or 0,
                    "agents_active": 0,  # sourced from agent runtime
                    "decisions_made": d_res.scalar_one_or_none() or 0,
                }
            except Exception as exc:
                _log.warning("digest._stats.failed", error=str(exc))
                return {
                    "missions_completed": 0,
                    "missions_started": 0,
                    "agents_active": 0,
                    "decisions_made": 0,
                }


# ─────────────────────────────────────────────────────────────────────────────
#  Summary builder
# ─────────────────────────────────────────────────────────────────────────────


def _build_summary(digest: WhileYouWereAwayDigest) -> str:
    since_str = digest.since.strftime("%H:%M")
    parts: list[str] = [f"Since {since_str}"]

    if digest.missions_completed:
        n = digest.missions_completed
        parts.append(f"{n} mission{'s' if n != 1 else ''} completed")
    if digest.missions_started:
        n = digest.missions_started
        parts.append(f"{n} new mission{'s' if n != 1 else ''} started")
    if digest.pending_approvals:
        n = len(digest.pending_approvals)
        parts.append(f"{n} approval{'s' if n != 1 else ''} await your review")
    if digest.blocked_items:
        n = len(digest.blocked_items)
        parts.append(f"{n} item{'s' if n != 1 else ''} blocked")
    if digest.decisions_made:
        parts.append(f"{digest.decisions_made} decisions made")
    if digest.total_cost_usd > 0:
        parts.append(f"${digest.total_cost_usd:.2f} spent")

    return (
        "No significant activity while you were away."
        if len(parts) == 1
        else ". ".join(parts) + "."
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Factory
# ─────────────────────────────────────────────────────────────────────────────


def get_digest_generator(
    session: AsyncSession,
    redis: Any | None = None,
) -> DigestGenerator:
    """Return a DigestGenerator bound to the given session."""
    return DigestGenerator(session=session, redis=redis)
