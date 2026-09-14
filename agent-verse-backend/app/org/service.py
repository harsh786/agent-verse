"""OrgService — CRUD and business logic for the AI Organization OS.

This service is intentionally layered on top of the existing AgentVerse
infrastructure. It delegates to existing services where possible:
- Goal execution → app.services.GoalService
- Agent creation → app.agent runtime
- Memory → app.memory
- Knowledge → app.knowledge
- Policy → app.governance
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

import structlog
from opentelemetry import trace
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.org.approval_chain import (
    ApprovalChain,
)
from app.org.models import (
    Organization,
    OrgBlueprint,
    OrgCapability,
    OrgDecision,
    OrgDepartment,
    OrgEvent,
    OrgMission,
    OrgMissionSchedule,
    OrgTask,
    OrgTeam,
    OrgWorkstream,
)

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


def resolve_llm_provider(app_state: Any) -> Any | None:
    """Resolve the real LLM provider from the request's wired ``app.state``.

    The org layer is meant to be genuinely LLM-driven (generic composer + generic
    mission decomposition), degrading to a deterministic heuristic only when no
    usable model is available. Earlier code read ``app_state.planner_provider``,
    an attribute that is *never* set on ``app.state`` — so the LLM path was dead
    and every org ran the template/heuristic fallback. The real provider that
    ``create_app`` binds is ``app.state._app_provider`` (see app/main.py). This
    helper prefers an explicit ``planner_provider`` (tests may inject one), then
    the canonical ``_app_provider``, then a per-request ``_llm_provider_override``,
    returning ``None`` when nothing is wired so the caller degrades honestly.

    A ``FakeProvider`` is intentionally returned as-is (not treated as ``None``):
    it exercises the real LLM code path deterministically, and the JSON-parse
    guards in the composer/decomposer degrade cleanly when its canned output is
    not a usable plan.
    """
    if app_state is None:
        return None
    for attr in ("planner_provider", "_app_provider", "_llm_provider_override"):
        provider = getattr(app_state, attr, None)
        if provider is not None:
            return provider
    return None


# ── Task status constants ─────────────────────────────────────────────────────
TASK_STATUSES = frozenset(
    {
        "draft",
        "queued",
        "planned",
        "assigned",
        "running",
        "waiting",
        "blocked",
        "review",
        "approval_required",
        "completed",
        "failed",
        "cancelled",
        "expired",
        "archived",
    }
)

MISSION_STATUSES = frozenset(
    {
        "draft",
        "queued",
        "planned",
        "active",
        "paused",
        "review",
        "completed",
        "failed",
        "cancelled",
        "archived",
    }
)

# ── Max recursion / anti-runaway limits ───────────────────────────────────────
MAX_TASK_DEPTH = 8
MAX_TASKS_PER_MISSION = 200


def _next_cron_fire(
    cron_expression: str, tz: str = "UTC", after: datetime | None = None
) -> datetime:
    """Next fire time (UTC-aware) for a cron expression evaluated in ``tz``."""
    from zoneinfo import ZoneInfo

    from croniter import croniter

    try:
        zone = ZoneInfo(tz or "UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    base = (after or datetime.now(UTC)).astimezone(zone)
    nxt = croniter(cron_expression, base).get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=zone)
    return nxt.astimezone(UTC)


class OrgService:
    """Service for the AI Organization Operating System.

    One instance per request (or per background worker).
    Requires an active AsyncSession with RLS context set.
    """

    def __init__(self, session: AsyncSession, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    # ── Internal helpers ─────────────────────────────────────────────────────

    async def _emit_event(
        self,
        org_id: uuid.UUID,
        event_type: str,
        *,
        title: str = "",
        description: str = "",
        entity_type: str | None = None,
        entity_id: str | None = None,
        severity: str = "info",
        payload: dict | None = None,
        source: str = "system",
        actor_id: str | None = None,
    ) -> OrgEvent:
        """Append a structured event to the org event bus."""
        ev = OrgEvent(
            tenant_id=self._tenant_id,
            org_id=org_id,
            event_type=event_type,
            title=title,
            description=description,
            entity_type=entity_type,
            entity_id=entity_id,
            severity=severity,
            payload=payload or {},
            source=source,
            actor_id=actor_id,
        )
        self._session.add(ev)
        await self._session.flush()
        # Bridge to the real-time SSE bus. _emit_event previously only wrote the DB
        # row, but the org events/stream SSE endpoint reads a Redis pub/sub channel
        # — so without this publish the live console never saw mission/team/agent
        # events (the "things moving on screen" experience was dead). Best-effort:
        # a publish failure must never break the write.
        await self._publish_realtime(
            org_id=org_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )
        return ev

    async def _publish_realtime(
        self,
        *,
        org_id: uuid.UUID,
        event_type: str,
        entity_type: str | None,
        entity_id: str | None,
        payload: dict | None,
    ) -> None:
        """Publish an org event onto the Redis SSE channel the frontend listens on.

        Maps the internal ``<entity>.<action>`` name to the ``org.<entity>.<action>``
        taxonomy the OrgRealtimeManager dispatches on, and threads the entity id into
        the payload (e.g. ``mission_id``) so the client invalidates the right query.
        """
        try:
            from app.org.events import get_org_event_publisher

            publisher = get_org_event_publisher()
            if getattr(publisher, "_redis", None) is None:
                return
            rt_payload = dict(payload or {})
            if entity_type and entity_id:
                rt_payload.setdefault(f"{entity_type}_id", str(entity_id))
            await publisher.publish(
                event_type=f"org.{event_type}",
                org_id=str(org_id),
                tenant_id=self._tenant_id,
                payload=rt_payload,
            )
        except Exception as exc:  # pragma: no cover - best-effort realtime bridge
            _log.debug("org_event_realtime_publish_failed", error=str(exc))

    # ── Organization CRUD ─────────────────────────────────────────────────────

    async def create_organization(
        self,
        *,
        name: str,
        slug: str | None = None,
        description: str = "",
        industry: str = "",
        jurisdiction: str = "",
        mission: str = "",
        vision: str = "",
        autonomy_level: int = 1,
        risk_tolerance: str = "medium",
        monthly_budget_usd: float = 0.0,
        goals: list[Any] | None = None,
        policies: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        blueprint_ids: list[str] | None = None,
        created_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Organization:
        """Create a new organization."""
        with _tracer.start_as_current_span("org.create_organization") as span:
            span.set_attribute("tenant_id", self._tenant_id)
            span.set_attribute("org.name", name)
            if not slug:
                slug = name.lower().replace(" ", "-")[:64]
            org = Organization(
                tenant_id=self._tenant_id,
                name=name,
                slug=slug,
                description=description,
                industry=industry,
                jurisdiction=jurisdiction,
                mission=mission,
                vision=vision,
                autonomy_level=max(0, min(5, autonomy_level)),
                risk_tolerance=risk_tolerance,
                monthly_budget_usd=monthly_budget_usd,
                goals=goals or [],
                policies=policies or {},
                settings=settings or {},
                blueprint_ids=blueprint_ids or [],
                created_by=created_by,
                metadata=metadata or {},
            )
            self._session.add(org)
            await self._session.flush()
            await self._emit_event(
                cast(uuid.UUID, org.id),
                "organization.created",
                title=f"Organization '{name}' created",
                entity_type="organization",
                entity_id=str(org.id),
            )
            span.set_attribute("org.id", str(org.id))
            _log.info("org_created tenant=%s org_id=%s name=%s", self._tenant_id, org.id, name)
            return org

    async def get_organization(self, org_id: str) -> Organization | None:
        result = await self._session.execute(
            select(Organization).where(
                and_(
                    Organization.tenant_id == self._tenant_id,
                    Organization.id == uuid.UUID(org_id),
                )
            )
        )
        return result.scalar_one_or_none()

    async def list_organizations(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Organization]:
        q = select(Organization).where(Organization.tenant_id == self._tenant_id)
        if status:
            q = q.where(Organization.status == status)
        q = q.order_by(Organization.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def update_organization(
        self, org_id: str, updates: dict[str, Any]
    ) -> Organization | None:
        org = await self.get_organization(org_id)
        if not org:
            return None
        for key, val in updates.items():
            if hasattr(org, key) and key not in ("id", "tenant_id", "created_at"):
                setattr(org, key, val)
        org.updated_at = datetime.now(UTC)
        await self._session.flush()
        return org

    async def delete_organization(self, org_id: str) -> bool:
        org = await self.get_organization(org_id)
        if not org:
            return False
        org.status = "archived"
        org.updated_at = datetime.now(UTC)
        await self._session.flush()
        return True

    # ── N2: Org Composer (NL → Organisation) ──────────────────────────────────

    # Industry-aware department blueprints for template composition. Each entry is
    # (name, purpose, capability_domains). Keep these small and sensible — the org
    # can be refined after creation via the normal department CRUD.
    _INDUSTRY_DEPARTMENTS: ClassVar[dict[str, list[tuple[str, str, list[str]]]]] = {
        "fintech": [
            ("Product", "Own the financial product roadmap", ["product", "strategy"]),
            ("Engineering", "Build and operate the platform", ["engineering", "software"]),
            ("Risk & Compliance", "Manage regulatory and financial risk", ["compliance", "risk"]),
            ("Growth", "Acquire and retain customers", ["marketing", "sales"]),
        ],
        "ecommerce": [
            ("Merchandising", "Curate catalog and pricing", ["merchandising", "pricing"]),
            ("Engineering", "Build storefront and fulfilment systems", ["engineering", "software"]),
            ("Marketing", "Drive demand and brand", ["marketing", "content"]),
            ("Operations", "Run fulfilment and support", ["operations", "support"]),
        ],
        "healthcare": [
            ("Clinical", "Own clinical quality and safety", ["clinical", "quality"]),
            ("Engineering", "Build compliant health systems", ["engineering", "software"]),
            ("Compliance", "Ensure HIPAA/regulatory compliance", ["compliance", "privacy"]),
            ("Operations", "Coordinate care delivery", ["operations", "coordination"]),
        ],
    }

    _DEFAULT_DEPARTMENTS: ClassVar[list[tuple[str, str, list[str]]]] = [
        ("Operations", "Coordinate execution across the org", ["operations", "coordination"]),
        ("Engineering", "Build and maintain products and systems", ["engineering", "software"]),
        ("Research", "Gather intelligence and analyse the market", ["research", "analysis"]),
        ("Growth", "Drive customer acquisition and revenue", ["marketing", "sales"]),
    ]

    # Keyword → extra department, so the description can shape the structure.
    _KEYWORD_DEPARTMENTS: ClassVar[list[tuple[tuple[str, ...], tuple[str, str, list[str]]]]] = [
        (("support", "customer", "success"),
         ("Customer Success", "Support and retain customers", ["support", "success"])),
        (("legal", "contract", "regulat"),
         ("Legal", "Handle legal, contracts, and regulation", ["legal", "compliance"])),
        (("finance", "budget", "accounting"),
         ("Finance", "Own budgeting and financial planning", ["finance", "accounting"])),
        (("data", "analytics", "ml", "ai model"),
         ("Data & Analytics", "Own data pipelines and insights", ["data", "analytics"])),
        (("design", "ux", "brand"),
         ("Design", "Own product and brand design", ["design", "ux"])),
    ]

    @staticmethod
    def _derive_org_name(description: str, industry: str) -> str:
        """Derive a short, human org name from the description (deterministic)."""
        cleaned = " ".join(description.strip().split())
        if not cleaned:
            return f"{industry.title()} Organisation" if industry else "AI Organisation"
        # First clause, capped to a reasonable length, title-cased.
        first = cleaned.split(".")[0].split(",")[0].strip()
        words = first.split()[:5]
        name = " ".join(words).title()
        return name[:80] or "AI Organisation"

    def _compose_departments(
        self, industry: str, description: str
    ) -> list[tuple[str, str, list[str]]]:
        """Pick a department blueprint by industry, then add keyword-driven extras."""
        base = self._INDUSTRY_DEPARTMENTS.get(industry.lower().strip(), self._DEFAULT_DEPARTMENTS)
        depts = list(base)
        existing = {d[0].lower() for d in depts}
        desc_lower = description.lower()
        for keywords, dept in self._KEYWORD_DEPARTMENTS:
            if any(k in desc_lower for k in keywords) and dept[0].lower() not in existing:
                depts.append(dept)
                existing.add(dept[0].lower())
        return depts

    async def _compose_departments_llm(
        self, description: str, industry: str, llm_provider: Any
    ) -> list[tuple[str, str, list[str]]]:
        """LLM-driven department composition for ANY objective. [] on failure.

        This is what makes the composer generalize beyond the hardcoded industry
        blueprints. The caller degrades to ``_compose_departments`` when this
        returns nothing (no provider, FakeProvider, or unparseable output).
        """
        import json

        from app.providers.base import CompletionRequest, Message

        prompt = (
            "Design a lean org department structure for the objective below. "
            "Return ONLY a JSON array (3-6 items) of objects: "
            '{"name": "<dept>", "purpose": "<one line>", '
            '"capability_domains": ["<domain>", ...]}.\n\n'
            f"INDUSTRY: {industry or 'general'}\nOBJECTIVE: {description}"
        )
        req = CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            model=getattr(llm_provider, "_default_model", "") or "claude-sonnet-4-5",
            max_tokens=600,
        )
        resp = await llm_provider.complete(req)
        raw = (resp.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        out: list[tuple[str, str, list[str]]] = []
        for item in data:
            if isinstance(item, dict) and item.get("name"):
                out.append(
                    (
                        str(item["name"])[:80],
                        str(item.get("purpose") or "")[:200],
                        [str(d) for d in (item.get("capability_domains") or [])],
                    )
                )
        return out

    async def compose_from_nl(
        self,
        *,
        description: str,
        goals: list[str] | None = None,
        industry: str = "",
        autonomy_level: int = 2,
        budget_usd: float = 0.0,
        constraints: list[str] | None = None,
        llm_provider: Any | None = None,
    ) -> dict[str, Any]:
        """Compose a whole organisation from a natural-language description.

        Generalized composition for ANY objective: when an ``llm_provider`` is
        supplied it drives an LLM department design; otherwise (or when the model
        yields nothing usable — e.g. FakeProvider) it degrades to a deterministic
        industry-appropriate blueprint with keyword-driven extras. Then it creates
        the org, its departments, and one initial mission per stated goal. This is
        the single reachable org composer — the per-mission team composer lives in
        MetaOrchestrator/TeamFormationEngine (also LLM-driven, generic).
        """
        goals = goals or []
        constraints = constraints or []
        with _tracer.start_as_current_span("org.compose_from_nl") as span:
            span.set_attribute("tenant_id", self._tenant_id)
            span.set_attribute("industry", industry)

            org = await self.create_organization(
                name=self._derive_org_name(description, industry),
                description=description,
                industry=industry,
                autonomy_level=autonomy_level,
                monthly_budget_usd=budget_usd,
                goals=list(goals),
                settings={"constraints": list(constraints), "composed_from_nl": True},
            )
            org_id = str(org.id)
            span.set_attribute("org.id", org_id)

            # Generalized composition: prefer the LLM design, degrade to template.
            composition_method = "template"
            dept_specs: list[tuple[str, str, list[str]]] = []
            if llm_provider is not None:
                try:
                    dept_specs = await self._compose_departments_llm(
                        description, industry, llm_provider
                    )
                    if len(dept_specs) >= 2:
                        composition_method = "llm"
                    else:
                        dept_specs = []
                except Exception as exc:  # pragma: no cover - provider variance
                    _log.warning("org.compose_from_nl.llm_failed", error=str(exc)[:120])
                    dept_specs = []
            if not dept_specs:
                dept_specs = self._compose_departments(industry, description)

            departments: list[dict[str, Any]] = []
            for name, purpose, domains in dept_specs:
                dept = await self.create_department(
                    org_id=org_id, name=name, purpose=purpose, capability_domains=list(domains)
                )
                departments.append(
                    {
                        "id": str(dept.id),
                        "name": dept.name,
                        "purpose": dept.purpose,
                        "capability_domains": list(dept.capability_domains or []),
                    }
                )

            initial_missions: list[dict[str, Any]] = []
            for goal in goals[:10]:
                goal_text = str(goal).strip()
                if not goal_text:
                    continue
                mission = await self.create_mission(
                    org_id=org_id,
                    title=goal_text[:200],
                    objective=goal_text,
                    priority="high",
                    source="composer",
                )
                initial_missions.append(
                    {"id": str(mission.id), "title": mission.title, "status": mission.status}
                )

            span.set_attribute("departments_created", len(departments))
            _log.info(
                "org_composed tenant=%s org_id=%s departments=%d missions=%d",
                self._tenant_id,
                org_id,
                len(departments),
                len(initial_missions),
            )
            return {
                "org_id": org_id,
                "name": org.name,
                "departments": departments,
                "initial_missions": initial_missions,
                "autonomy_level": org.autonomy_level,
                "status": "ready",
                "composition_method": composition_method,
            }

    # ── Department CRUD ───────────────────────────────────────────────────────

    async def create_department(
        self,
        *,
        org_id: str,
        name: str,
        purpose: str = "",
        capability_domains: list[str] | None = None,
        parent_dept_id: str | None = None,
        manager_agent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrgDepartment:
        with _tracer.start_as_current_span("org.create_department") as span:
            span.set_attribute("tenant_id", self._tenant_id)
            span.set_attribute("org_id", org_id)
            dept = OrgDepartment(
                tenant_id=self._tenant_id,
                org_id=uuid.UUID(org_id),
                name=name,
                purpose=purpose,
                capability_domains=capability_domains or [],
                parent_dept_id=uuid.UUID(parent_dept_id) if parent_dept_id else None,
                manager_agent_id=manager_agent_id,
                metadata=metadata or {},
            )
            self._session.add(dept)
            await self._session.flush()
            await self._emit_event(
                uuid.UUID(org_id),
                "department.created",
                title=f"Department '{name}' created",
                entity_type="department",
                entity_id=str(dept.id),
            )
            span.set_attribute("dept.id", str(dept.id))
            return dept

    async def list_departments(self, org_id: str) -> list[OrgDepartment]:
        result = await self._session.execute(
            select(OrgDepartment)
            .where(
                and_(
                    OrgDepartment.tenant_id == self._tenant_id,
                    OrgDepartment.org_id == uuid.UUID(org_id),
                    OrgDepartment.status == "active",
                )
            )
            .order_by(OrgDepartment.name)
        )
        return list(result.scalars().all())

    async def get_department(self, dept_id: str) -> OrgDepartment | None:
        result = await self._session.execute(
            select(OrgDepartment).where(
                and_(
                    OrgDepartment.tenant_id == self._tenant_id,
                    OrgDepartment.id == uuid.UUID(dept_id),
                )
            )
        )
        return result.scalar_one_or_none()

    async def update_department(
        self, dept_id: str, updates: dict[str, Any]
    ) -> OrgDepartment | None:
        dept = await self.get_department(dept_id)
        if not dept:
            return None
        for key, val in updates.items():
            if hasattr(dept, key) and key not in ("id", "tenant_id", "created_at"):
                setattr(dept, key, val)
        dept.updated_at = datetime.now(UTC)
        await self._session.flush()
        return dept

    # ── Team CRUD ─────────────────────────────────────────────────────────────

    async def create_team(
        self,
        *,
        org_id: str,
        name: str,
        purpose: str = "",
        dept_id: str | None = None,
        team_type: str = "persistent",
        manager_agent_id: str | None = None,
        member_agent_ids: list[str] | None = None,
        capability_ids: list[str] | None = None,
        tool_ids: list[str] | None = None,
        model_config: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrgTeam:
        with _tracer.start_as_current_span("org.create_team") as span:
            span.set_attribute("tenant_id", self._tenant_id)
            span.set_attribute("org_id", org_id)
            span.set_attribute("team.type", team_type)
        team = OrgTeam(
            tenant_id=self._tenant_id,
            org_id=uuid.UUID(org_id),
            dept_id=uuid.UUID(dept_id) if dept_id else None,
            name=name,
            purpose=purpose,
            team_type=team_type,
            manager_agent_id=manager_agent_id,
            member_agent_ids=member_agent_ids or [],
            capability_ids=capability_ids or [],
            tool_ids=tool_ids or [],
            model_config=model_config or {},
            extra_data=metadata or {},
        )
        self._session.add(team)
        await self._session.flush()
        await self._emit_event(
            uuid.UUID(org_id),
            "team.created",
            title=f"Team '{name}' formed",
            entity_type="team",
            entity_id=str(team.id),
        )
        return team

    async def list_teams(
        self,
        org_id: str,
        *,
        dept_id: str | None = None,
        status: str | None = "active",
    ) -> list[OrgTeam]:
        q = select(OrgTeam).where(
            and_(
                OrgTeam.tenant_id == self._tenant_id,
                OrgTeam.org_id == uuid.UUID(org_id),
            )
        )
        if dept_id:
            q = q.where(OrgTeam.dept_id == uuid.UUID(dept_id))
        if status:
            q = q.where(OrgTeam.status == status)
        result = await self._session.execute(q.order_by(OrgTeam.name))
        return list(result.scalars().all())

    async def get_team(self, team_id: str) -> OrgTeam | None:
        result = await self._session.execute(
            select(OrgTeam).where(
                and_(
                    OrgTeam.tenant_id == self._tenant_id,
                    OrgTeam.id == uuid.UUID(team_id),
                )
            )
        )
        return result.scalar_one_or_none()

    async def update_team(self, team_id: str, updates: dict[str, Any]) -> OrgTeam | None:
        team = await self.get_team(team_id)
        if not team:
            return None
        for key, val in updates.items():
            if hasattr(team, key) and key not in ("id", "tenant_id", "created_at"):
                setattr(team, key, val)
        team.updated_at = datetime.now(UTC)
        await self._session.flush()
        return team

    async def get_team_member_profiles(
        self,
        *,
        org_id: str,
        team_id: str,
    ) -> list[dict[str, Any]]:
        """Return best-effort member profiles backed by real org task data.

        The org domain currently persists member IDs on teams but does not own a
        dedicated agent profile table. This method enriches members with runtime
        status and current task by looking at active team tasks.
        """
        team = await self.get_team(team_id)
        if team is None:
            return []

        member_ids = list(getattr(team, "member_agent_ids", None) or [])
        if not member_ids:
            return []

        active_task_statuses = [
            "queued",
            "planned",
            "assigned",
            "running",
            "waiting",
            "blocked",
            "review",
            "approval_required",
        ]
        result = await self._session.execute(
            select(OrgTask)
            .where(
                and_(
                    OrgTask.tenant_id == self._tenant_id,
                    OrgTask.org_id == uuid.UUID(org_id),
                    OrgTask.assigned_team_id == uuid.UUID(team_id),
                    OrgTask.status.in_(active_task_statuses),
                )
            )
            .order_by(OrgTask.updated_at.desc())
        )
        tasks = list(result.scalars().all())

        latest_task_by_member: dict[str, OrgTask] = {}
        member_set = set(member_ids)
        for task in tasks:
            for agent_id in list(task.assigned_agent_ids or []):
                if agent_id in member_set and agent_id not in latest_task_by_member:
                    latest_task_by_member[agent_id] = task

        status_map = {
            "running": "executing",
            "assigned": "planning",
            "planned": "planning",
            "queued": "waiting",
            "waiting": "waiting",
            "blocked": "blocked",
            "review": "waiting",
            "approval_required": "waiting",
        }

        role_map = dict((getattr(team, "extra_data", None) or {}).get("member_roles", {}))
        members: list[dict[str, Any]] = []
        for index, member_id in enumerate(member_ids):
            task = latest_task_by_member.get(member_id)
            status = status_map.get(task.status, "idle") if task else "idle"
            members.append(
                {
                    "id": member_id,
                    "name": f"Agent {index + 1}",
                    "role": role_map.get(member_id, "Mission specialist"),
                    "status": status,
                    "current_task": task.title if task else None,
                }
            )
        return members

    # ── Capability CRUD ───────────────────────────────────────────────────────

    async def create_capability(
        self,
        *,
        org_id: str | None = None,
        name: str,
        description: str = "",
        domain: str = "",
        skills: list[str] | None = None,
        required_tools: list[str] | None = None,
        required_models: list[str] | None = None,
        required_knowledge: list[str] | None = None,
        needs_human_approval: bool = False,
        risk_level: str = "low",
        metadata: dict[str, Any] | None = None,
    ) -> OrgCapability:
        cap = OrgCapability(
            tenant_id=self._tenant_id,
            org_id=uuid.UUID(org_id) if org_id else None,
            name=name,
            description=description,
            domain=domain,
            skills=skills or [],
            required_tools=required_tools or [],
            required_models=required_models or [],
            required_knowledge=required_knowledge or [],
            needs_human_approval=needs_human_approval,
            risk_level=risk_level,
            metadata=metadata or {},
        )
        self._session.add(cap)
        await self._session.flush()
        return cap

    async def list_capabilities(
        self,
        org_id: str | None = None,
        *,
        domain: str | None = None,
    ) -> list[OrgCapability]:
        q = select(OrgCapability).where(OrgCapability.tenant_id == self._tenant_id)
        if org_id:
            q = q.where(OrgCapability.org_id == uuid.UUID(org_id))
        if domain:
            q = q.where(OrgCapability.domain == domain)
        result = await self._session.execute(q.order_by(OrgCapability.name))
        return list(result.scalars().all())

    # ── Mission CRUD ──────────────────────────────────────────────────────────

    async def create_mission(
        self,
        *,
        org_id: str,
        title: str,
        objective: str = "",
        why: str = "",
        expected_outcome: str = "",
        priority: str = "medium",
        dept_id: str | None = None,
        assigned_team_id: str | None = None,
        autonomy_level: int | None = None,
        source: str = "manual",
        status: str | None = None,
        success_criteria: list[Any] | None = None,
        budget_usd: float | None = None,
        deadline: datetime | None = None,
        tags: list[str] | None = None,
        created_by: str | None = None,
        trigger_event: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrgMission:
        with _tracer.start_as_current_span("org.create_mission") as span:
            span.set_attribute("tenant_id", self._tenant_id)
            span.set_attribute("org_id", org_id)
            span.set_attribute("mission.priority", priority)
            span.set_attribute("mission.source", source)
            mission_kwargs = {
                "tenant_id": self._tenant_id,
                "org_id": uuid.UUID(org_id),
                "dept_id": uuid.UUID(dept_id) if dept_id else None,
                "title": title,
                "objective": objective,
                "why": why,
                "expected_outcome": expected_outcome,
                "priority": priority,
                "assigned_team_id": uuid.UUID(assigned_team_id) if assigned_team_id else None,
                "autonomy_level": autonomy_level,
                "source": source,
                "success_criteria": success_criteria or [],
                "budget_usd": budget_usd,
                "deadline": deadline,
                "tags": tags or [],
                "created_by": created_by,
                "trigger_event": trigger_event,
                "metadata": metadata or {},
            }
            if status is not None:
                mission_kwargs["status"] = status
            mission = OrgMission(**mission_kwargs)
            self._session.add(mission)
            await self._session.flush()
            await self._emit_event(
                uuid.UUID(org_id),
                "mission.created",
                title=f"Mission '{title}' created",
                entity_type="mission",
                entity_id=str(mission.id),
                payload={"source": source, "priority": priority},
            )
            span.set_attribute("mission.id", str(mission.id))
            _log.info(
                "mission_created tenant=%s org=%s mission=%s title=%s",
                self._tenant_id,
                org_id,
                mission.id,
                title,
            )
            return mission

    async def get_mission(self, mission_id: str) -> OrgMission | None:
        result = await self._session.execute(
            select(OrgMission).where(
                and_(
                    OrgMission.tenant_id == self._tenant_id,
                    OrgMission.id == uuid.UUID(mission_id),
                )
            )
        )
        return result.scalar_one_or_none()

    # ── Mission schedules (autonomous, cron-driven) ──────────────────────────

    async def create_mission_schedule(
        self,
        *,
        org_id: str,
        title: str,
        objective: str = "",
        cron_expression: str,
        timezone: str = "UTC",
        priority: str = "medium",
        autonomy_level: int | None = None,
        dept_id: str | None = None,
        name: str = "",
        enabled: bool = True,
        publish_config: dict[str, Any] | None = None,
    ) -> OrgMissionSchedule:
        """Create a cron schedule that autonomously launches an org mission.

        ``publish_config`` (optional) makes each run publish its deliverable via
        a connector: ``{connector_server_id, tool_name, arguments}`` where any
        argument value equal to ``"{{deliverable}}"`` is replaced with the
        mission's deliverable text. It starts unapproved (``approved=False``) —
        the first run is held until the org approves publishing for this
        schedule, after which runs publish autonomously.
        """
        next_fire = _next_cron_fire(cron_expression, timezone) if enabled else None
        pub: dict[str, Any] | None = None
        if publish_config and publish_config.get("connector_server_id") and publish_config.get(
            "tool_name"
        ):
            pub = {
                "connector_server_id": str(publish_config["connector_server_id"]),
                "tool_name": str(publish_config["tool_name"]),
                "arguments": publish_config.get("arguments") or {},
                "approved": bool(publish_config.get("approved", False)),
            }
        sched = OrgMissionSchedule(
            tenant_id=uuid.UUID(self._tenant_id),
            org_id=uuid.UUID(org_id),
            name=name or title,
            title=title,
            objective=objective,
            priority=priority,
            autonomy_level=autonomy_level,
            dept_id=uuid.UUID(dept_id) if dept_id else None,
            cron_expression=cron_expression,
            timezone=timezone,
            enabled=enabled,
            next_fire_at=next_fire,
            publish_config=pub,
        )
        self._session.add(sched)
        await self._session.flush()
        await self._emit_event(
            uuid.UUID(org_id),
            "schedule.created",
            title=f"Schedule '{sched.name}' created",
            entity_type="schedule",
            entity_id=str(sched.id),
            payload={"cron": cron_expression, "timezone": timezone, "next_fire_at": (
                next_fire.isoformat() if next_fire else None
            )},
        )
        return sched

    async def list_mission_schedules(self, org_id: str) -> list[OrgMissionSchedule]:
        result = await self._session.execute(
            select(OrgMissionSchedule)
            .where(
                and_(
                    OrgMissionSchedule.tenant_id == self._tenant_id,
                    OrgMissionSchedule.org_id == uuid.UUID(org_id),
                )
            )
            .order_by(OrgMissionSchedule.created_at.desc())
        )
        return list(result.scalars().all())

    async def set_mission_schedule_enabled(
        self, org_id: str, schedule_id: str, enabled: bool
    ) -> OrgMissionSchedule | None:
        sched = (
            await self._session.execute(
                select(OrgMissionSchedule).where(
                    and_(
                        OrgMissionSchedule.tenant_id == self._tenant_id,
                        # Scope by org_id too — the path org must own the schedule,
                        # so one org can't toggle another org's schedule (IDOR).
                        OrgMissionSchedule.org_id == uuid.UUID(org_id),
                        OrgMissionSchedule.id == uuid.UUID(schedule_id),
                    )
                )
            )
        ).scalar_one_or_none()
        if sched is None:
            return None
        sched.enabled = enabled
        # Re-arm the next fire when enabling; clear it when pausing.
        sched.next_fire_at = (
            _next_cron_fire(str(sched.cron_expression), str(sched.timezone)) if enabled else None
        )
        sched.updated_at = datetime.now(UTC)
        await self._session.flush()
        return sched

    async def delete_mission_schedule(self, org_id: str, schedule_id: str) -> bool:
        sched = (
            await self._session.execute(
                select(OrgMissionSchedule).where(
                    and_(
                        OrgMissionSchedule.tenant_id == self._tenant_id,
                        # Scope by org_id too (IDOR guard — see set_..._enabled).
                        OrgMissionSchedule.org_id == uuid.UUID(org_id),
                        OrgMissionSchedule.id == uuid.UUID(schedule_id),
                    )
                )
            )
        ).scalar_one_or_none()
        if sched is None:
            return False
        await self._session.delete(sched)
        await self._session.flush()
        return True

    async def get_mission_schedule(
        self, org_id: str, schedule_id: str
    ) -> OrgMissionSchedule | None:
        return (
            await self._session.execute(
                select(OrgMissionSchedule).where(
                    and_(
                        OrgMissionSchedule.tenant_id == self._tenant_id,
                        OrgMissionSchedule.org_id == uuid.UUID(org_id),
                        OrgMissionSchedule.id == uuid.UUID(schedule_id),
                    )
                )
            )
        ).scalar_one_or_none()

    async def approve_schedule_publishing(
        self, org_id: str, schedule_id: str, approved: bool = True
    ) -> OrgMissionSchedule | None:
        """Flip the one-time publish approval gate for a schedule.

        The first scheduled run holds its deliverable at an approval gate rather
        than publishing. Approving here lets this run's held deliverable (and all
        future runs) publish autonomously via the configured connector. Revoking
        (``approved=False``) re-arms the gate for subsequent runs.
        """
        sched = await self.get_mission_schedule(org_id, schedule_id)
        if sched is None:
            return None
        pub = dict(sched.publish_config or {})
        if not pub.get("connector_server_id") or not pub.get("tool_name"):
            # Nothing to approve — this schedule has no publish target configured.
            return sched
        pub["approved"] = bool(approved)
        sched.publish_config = pub
        sched.updated_at = datetime.now(UTC)
        await self._session.flush()
        return sched

    async def release_pending_publish_missions(
        self, org_id: str, schedule_id: str
    ) -> list[str]:
        """Approve + return finished missions of this schedule held at the gate.

        The first scheduled run finalizes while its publish is still unapproved,
        so its deliverable waits with ``extra_data.publish_pending = true``.
        Approving the schedule releases those held missions: we flip each one's
        stamped ``publish.approved`` to true and clear the pending flag, then
        return their ids so the caller can enqueue the publish task. Scoped by
        org + schedule id (IDOR guard) and idempotent (already-published missions
        are skipped).
        """
        rows = (
            await self._session.execute(
                select(OrgMission).where(
                    and_(
                        OrgMission.tenant_id == self._tenant_id,
                        OrgMission.org_id == uuid.UUID(org_id),
                        OrgMission.status == "completed",
                        OrgMission.extra_data["publish"]["schedule_id"].astext == schedule_id,
                        OrgMission.extra_data["publish_pending"].astext == "true",
                    )
                )
            )
        ).scalars().all()
        released: list[str] = []
        for m in rows:
            extra = dict(m.extra_data or {})
            pub = extra.get("publish")
            if not isinstance(pub, dict) or extra.get("published"):
                continue
            pub = {**pub, "approved": True}
            extra["publish"] = pub
            extra.pop("publish_pending", None)
            m.extra_data = extra
            released.append(str(m.id))
        if released:
            await self._session.flush()
        return released

    async def list_missions(
        self,
        org_id: str,
        *,
        status: str | None = None,
        exclude_statuses: list[str] | None = None,
        priority: str | None = None,
        dept_id: str | None = None,
        source: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OrgMission]:
        q = select(OrgMission).where(
            and_(
                OrgMission.tenant_id == self._tenant_id,
                OrgMission.org_id == uuid.UUID(org_id),
            )
        )
        if status:
            q = q.where(OrgMission.status == status)
        # ``exclude_statuses`` (e.g. the org brain's non-terminal-mission scan
        # for dedup — see app/org/brain.py) is independent of ``status`` so
        # both can be combined; NOT IN is more robust to new statuses being
        # added later than enumerating every non-terminal one.
        if exclude_statuses:
            q = q.where(OrgMission.status.notin_(exclude_statuses))
        if source:
            q = q.where(OrgMission.source == source)
        if priority:
            q = q.where(OrgMission.priority == priority)
        if dept_id:
            q = q.where(OrgMission.dept_id == uuid.UUID(dept_id))
        q = q.order_by(OrgMission.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def update_mission_status(self, mission_id: str, status: str) -> OrgMission | None:
        with _tracer.start_as_current_span("org.update_mission_status") as span:
            span.set_attribute("tenant_id", self._tenant_id)
            span.set_attribute("mission_id", mission_id)
            span.set_attribute("new_status", status)
            if status not in MISSION_STATUSES:
                raise ValueError(f"Invalid mission status: {status!r}")
            mission = await self.get_mission(mission_id)
            if not mission:
                return None
            old_status = str(mission.status)
            mission.status = status
            mission.updated_at = datetime.now(UTC)
            if status == "active" and not mission.started_at:
                mission.started_at = datetime.now(UTC)
            if status in ("completed", "failed", "cancelled"):
                mission.completed_at = datetime.now(UTC)
            await self._session.flush()
            # The canonical lifecycle event for a mission going active is
            # ``mission.started`` (→ org.mission.started), which the UI/JARVIS
            # narrate — not ``mission.active``. Map active→started here and carry
            # the clean mission title in the payload so consumers can render it.
            event_action = "started" if status == "active" else status
            await self._emit_event(
                cast(uuid.UUID, mission.org_id),
                f"mission.{event_action}",
                title=f"Mission '{mission.title}' -> {status}",
                entity_type="mission",
                entity_id=mission_id,
                severity="warning" if status == "failed" else "info",
                payload={
                    "old_status": old_status,
                    "new_status": status,
                    "title": mission.title,
                },
            )
            span.set_attribute("old_status", old_status)
            return mission

    # ── Task CRUD ─────────────────────────────────────────────────────────────

    async def create_task(
        self,
        *,
        org_id: str,
        title: str,
        objective: str = "",
        why: str = "",
        mission_id: str | None = None,
        workstream_id: str | None = None,
        parent_task_id: str | None = None,
        priority: str = "medium",
        assigned_team_id: str | None = None,
        assigned_agent_ids: list[str] | None = None,
        owner_agent_id: str | None = None,
        required_capabilities: list[str] | None = None,
        required_tools: list[str] | None = None,
        required_models: list[str] | None = None,
        success_criteria: list[Any] | None = None,
        budget_usd: float | None = None,
        cost_estimate_usd: float | None = None,
        risk_level: str = "low",
        risk_notes: str = "",
        deadline: datetime | None = None,
        dependencies: list[str] | None = None,
        depth: int = 0,
        linked_goal_id: str | None = None,
        expires_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrgTask:
        with _tracer.start_as_current_span("org.create_task") as span:
            span.set_attribute("tenant_id", self._tenant_id)
            span.set_attribute("org_id", org_id)
            span.set_attribute("task.priority", priority)
            span.set_attribute("task.depth", depth)
            # Anti-runaway: depth limit
            if depth > MAX_TASK_DEPTH:
                raise ValueError(f"Task depth {depth} exceeds maximum {MAX_TASK_DEPTH}")

            # Anti-runaway: per-mission task count
            if mission_id:
                count_result = await self._session.execute(
                    select(func.count(OrgTask.id)).where(
                        and_(
                            OrgTask.tenant_id == self._tenant_id,
                            OrgTask.mission_id == uuid.UUID(mission_id),
                        )
                    )
                )
                count = count_result.scalar() or 0
                if count >= MAX_TASKS_PER_MISSION:
                    raise ValueError(
                        f"Mission already has {count} tasks (max {MAX_TASKS_PER_MISSION})"
                    )

            task = OrgTask(
                tenant_id=self._tenant_id,
                org_id=uuid.UUID(org_id),
                mission_id=uuid.UUID(mission_id) if mission_id else None,
                workstream_id=uuid.UUID(workstream_id) if workstream_id else None,
                parent_task_id=uuid.UUID(parent_task_id) if parent_task_id else None,
                title=title,
                objective=objective,
                why=why,
                priority=priority,
                assigned_team_id=uuid.UUID(assigned_team_id) if assigned_team_id else None,
                assigned_agent_ids=assigned_agent_ids or [],
                owner_agent_id=owner_agent_id,
                required_capabilities=required_capabilities or [],
                required_tools=required_tools or [],
                required_models=required_models or [],
                success_criteria=success_criteria or [],
                budget_usd=budget_usd,
                cost_estimate_usd=cost_estimate_usd,
                risk_level=risk_level,
                risk_notes=risk_notes,
                deadline=deadline,
                dependencies=dependencies or [],
                depth=depth,
                linked_goal_id=linked_goal_id,
                expires_at=expires_at,
                # NOTE: OrgTask has no "metadata" column — that name is the
                # SQLAlchemy declarative Base.metadata registry. The JSONB scratch
                # field is "extra_data"; passing metadata= only set a transient
                # shadow attribute that was NEVER persisted (so task metadata was
                # silently lost on reload). Write the real column.
                extra_data=metadata or {},
            )
            self._session.add(task)
            await self._session.flush()
            await self._emit_event(
                uuid.UUID(org_id),
                "task.created",
                title=f"Task '{title}' created",
                entity_type="task",
                entity_id=str(task.id),
                payload={"priority": priority, "risk_level": risk_level, "depth": depth},
            )
            span.set_attribute("task.id", str(task.id))
            return task

    async def get_task(self, task_id: str) -> OrgTask | None:
        result = await self._session.execute(
            select(OrgTask).where(
                and_(
                    OrgTask.tenant_id == self._tenant_id,
                    OrgTask.id == uuid.UUID(task_id),
                )
            )
        )
        return result.scalar_one_or_none()

    async def list_tasks(
        self,
        org_id: str,
        *,
        mission_id: str | None = None,
        workstream_id: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        assigned_team_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[OrgTask]:
        q = select(OrgTask).where(
            and_(
                OrgTask.tenant_id == self._tenant_id,
                OrgTask.org_id == uuid.UUID(org_id),
            )
        )
        if mission_id:
            q = q.where(OrgTask.mission_id == uuid.UUID(mission_id))
        if workstream_id:
            q = q.where(OrgTask.workstream_id == uuid.UUID(workstream_id))
        if status:
            q = q.where(OrgTask.status == status)
        if priority:
            q = q.where(OrgTask.priority == priority)
        if assigned_team_id:
            q = q.where(OrgTask.assigned_team_id == uuid.UUID(assigned_team_id))
        q = q.order_by(OrgTask.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        *,
        evidence: list[Any] | None = None,
        outputs: list[Any] | None = None,
        actual_cost_usd: float | None = None,
    ) -> OrgTask | None:
        if status not in TASK_STATUSES:
            raise ValueError(f"Invalid task status: {status!r}")
        task = await self.get_task(task_id)
        if not task:
            return None
        old_status = task.status
        task.status = status
        task.updated_at = datetime.now(UTC)
        if status == "running" and not task.started_at:
            task.started_at = datetime.now(UTC)
        if status in ("completed", "failed", "cancelled", "expired"):
            task.completed_at = datetime.now(UTC)
        if evidence:
            task.evidence = (task.evidence or []) + evidence
        if outputs:
            task.outputs = (task.outputs or []) + outputs
        if actual_cost_usd is not None:
            task.actual_cost_usd = actual_cost_usd
        # Append to audit trail
        task.audit_trail = (task.audit_trail or []) + [
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "old_status": old_status,
                "new_status": status,
            }
        ]
        await self._session.flush()
        await self._emit_event(
            cast(uuid.UUID, task.org_id),
            f"task.{status}",
            title=f"Task '{task.title}' → {status}",
            entity_type="task",
            entity_id=task_id,
            severity="warning" if status in ("failed", "blocked") else "info",
        )
        return task

    # ── Decision CRUD ─────────────────────────────────────────────────────────

    async def record_decision(
        self,
        *,
        org_id: str,
        entity_type: str,
        entity_id: str,
        decision_type: str,
        description: str = "",
        why: str = "",
        trigger: dict[str, Any] | None = None,
        evidence: list[Any] | None = None,
        policy_refs: list[str] | None = None,
        expected_outcome: str = "",
        risk_level: str = "low",
        cost_estimate_usd: float | None = None,
        autonomy_level: int | None = None,
        approval_status: str = "auto_approved",
        actor_agent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrgDecision:
        decision = OrgDecision(
            tenant_id=self._tenant_id,
            org_id=uuid.UUID(org_id),
            entity_type=entity_type,
            entity_id=entity_id,
            decision_type=decision_type,
            description=description,
            why=why,
            trigger=trigger,
            evidence=evidence or [],
            policy_refs=policy_refs or [],
            expected_outcome=expected_outcome,
            risk_level=risk_level,
            cost_estimate_usd=cost_estimate_usd,
            autonomy_level=autonomy_level,
            approval_status=approval_status,
            actor_agent_id=actor_agent_id,
            metadata=metadata or {},
        )
        self._session.add(decision)
        await self._session.flush()
        return decision

    async def list_decisions(
        self,
        org_id: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        approval_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OrgDecision]:
        q = select(OrgDecision).where(
            and_(
                OrgDecision.tenant_id == self._tenant_id,
                OrgDecision.org_id == uuid.UUID(org_id),
            )
        )
        if entity_type:
            q = q.where(OrgDecision.entity_type == entity_type)
        if entity_id:
            q = q.where(OrgDecision.entity_id == entity_id)
        if approval_status:
            q = q.where(OrgDecision.approval_status == approval_status)
        q = q.order_by(OrgDecision.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(q)
        return list(result.scalars().all())

    # ── Events ────────────────────────────────────────────────────────────────

    async def record_collaboration_event(
        self,
        org_id: str | uuid.UUID,
        *,
        from_agent: str,
        kind: str,
        message: str,
        payload: dict[str, Any],
    ) -> OrgEvent:
        """Persist one ambient collaboration message (Task 9's "team talks"
        chatter) as an ``org_events`` row, for the Situation Room Team
        Channel history and the per-agent audit trail.

        DB-only insert — deliberately does NOT go through ``_emit_event``,
        which also bridges to the realtime SSE bus via
        ``_publish_realtime``. ``CollaborationTick`` already publishes the
        same enriched payload to SSE itself (``CollaborationEventPublisher
        .publish``), so routing this persistence through ``_emit_event``
        would double-publish the same message onto the org's live event
        stream. Uses the caller's already-open session (the tick's
        RLS-scoped transaction), so this insert commits atomically with the
        rest of the tick rather than opening a second transaction.
        """
        ev = OrgEvent(
            tenant_id=self._tenant_id,
            org_id=uuid.UUID(org_id) if isinstance(org_id, str) else org_id,
            event_type="org.collaboration.message",
            title=f"{from_agent} · {kind}",
            description=(message or "")[:500],
            entity_type="agent",
            entity_id=from_agent,
            severity="info",
            payload=payload or {},
            source="collaboration",
            actor_id=from_agent,
        )
        self._session.add(ev)
        await self._session.flush()
        return ev

    async def list_events(
        self,
        org_id: str,
        *,
        event_type: str | None = None,
        severity: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[OrgEvent]:
        q = select(OrgEvent).where(
            and_(
                OrgEvent.tenant_id == self._tenant_id,
                OrgEvent.org_id == uuid.UUID(org_id),
            )
        )
        if event_type:
            q = q.where(OrgEvent.event_type == event_type)
        if severity:
            q = q.where(OrgEvent.severity == severity)
        if since:
            q = q.where(OrgEvent.created_at >= since)
        q = q.order_by(OrgEvent.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(q)
        return list(result.scalars().all())

    # ── Organization health summary ───────────────────────────────────────────

    async def get_org_health(self, org_id: str) -> dict[str, Any]:
        """Return a health summary for the organization command center."""
        with _tracer.start_as_current_span("org.get_org_health") as span:
            span.set_attribute("tenant_id", self._tenant_id)
            span.set_attribute("org_id", org_id)
            uid = uuid.UUID(org_id)

            # Active missions
            mission_result = await self._session.execute(
                select(func.count(OrgMission.id)).where(
                    and_(
                        OrgMission.tenant_id == self._tenant_id,
                        OrgMission.org_id == uid,
                        OrgMission.status == "active",
                    )
                )
            )

            # Tasks by status
            task_result = await self._session.execute(
                select(OrgTask.status, func.count(OrgTask.id))
                .where(
                    and_(
                        OrgTask.tenant_id == self._tenant_id,
                        OrgTask.org_id == uid,
                        OrgTask.status.not_in(["archived", "cancelled"]),
                    )
                )
                .group_by(OrgTask.status)
            )

            # Active teams
            team_result = await self._session.execute(
                select(func.count(OrgTeam.id)).where(
                    and_(
                        OrgTeam.tenant_id == self._tenant_id,
                        OrgTeam.org_id == uid,
                        OrgTeam.status == "active",
                    )
                )
            )

            # Recent events (last 24h)
            from datetime import timedelta

            since_24h = datetime.now(UTC) - timedelta(hours=24)
            event_result = await self._session.execute(
                select(OrgEvent.severity, func.count(OrgEvent.id))
                .where(
                    and_(
                        OrgEvent.tenant_id == self._tenant_id,
                        OrgEvent.org_id == uid,
                        OrgEvent.created_at >= since_24h,
                    )
                )
                .group_by(OrgEvent.severity)
            )

            # Pending approvals — only those on a still-open mission. A completed/
            # failed mission must not keep a leftover approval gate counted as
            # "pending" (that made the dashboard show a phantom approval forever).
            approval_result = await self._session.execute(
                select(func.count(OrgTask.id))
                .select_from(OrgTask)
                .outerjoin(OrgMission, OrgMission.id == OrgTask.mission_id)
                .where(
                    and_(
                        OrgTask.tenant_id == self._tenant_id,
                        OrgTask.org_id == uid,
                        OrgTask.status == "approval_required",
                        or_(
                            OrgTask.mission_id.is_(None),
                            OrgMission.status.not_in(
                                ("completed", "failed", "cancelled", "archived")
                            ),
                        ),
                    )
                )
            )

            task_counts: dict[str, int] = {}
            for status, cnt in task_result.all():
                task_counts[status] = cnt

            event_counts: dict[str, int] = {}
            for severity, cnt in event_result.all():
                event_counts[severity] = cnt

            active_missions = mission_result.scalar() or 0
            active_teams = team_result.scalar() or 0
            pending_approvals = approval_result.scalar() or 0

            overall_health = "healthy"
            if task_counts.get("failed", 0) > 0 or event_counts.get("critical", 0) > 0:
                overall_health = "degraded"
            if pending_approvals > 5:
                overall_health = "attention_needed"

            health_data = {
                "health": overall_health,
                "active_missions": active_missions,
                "active_teams": active_teams,
                "pending_approvals": pending_approvals,
                "task_counts": task_counts,
                "event_counts_24h": event_counts,
                "items_needing_attention": pending_approvals + task_counts.get("blocked", 0),
            }
            span.set_attribute("health", overall_health)
            span.set_attribute("active_missions", active_missions)
            return health_data

    # ── Blueprint CRUD ────────────────────────────────────────────────────────

    async def list_blueprints(self, *, domain: str | None = None) -> list[OrgBlueprint]:
        q = select(OrgBlueprint)
        if domain:
            q = q.where(OrgBlueprint.domain == domain)
        result = await self._session.execute(q.order_by(OrgBlueprint.name))
        return list(result.scalars().all())

    async def get_blueprint(self, blueprint_id: str) -> OrgBlueprint | None:
        result = await self._session.execute(
            select(OrgBlueprint).where(OrgBlueprint.id == uuid.UUID(blueprint_id))
        )
        return result.scalar_one_or_none()

    async def get_blueprint_by_slug(self, slug: str) -> OrgBlueprint | None:
        result = await self._session.execute(select(OrgBlueprint).where(OrgBlueprint.slug == slug))
        return result.scalar_one_or_none()

    # ── Workstream CRUD ───────────────────────────────────────────────────────

    async def create_workstream(
        self,
        *,
        org_id: str,
        mission_id: str,
        title: str,
        description: str = "",
        assigned_team_id: str | None = None,
        order_index: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> OrgWorkstream:
        ws = OrgWorkstream(
            tenant_id=self._tenant_id,
            org_id=uuid.UUID(org_id),
            mission_id=uuid.UUID(mission_id),
            title=title,
            description=description,
            assigned_team_id=uuid.UUID(assigned_team_id) if assigned_team_id else None,
            order_index=order_index,
            metadata=metadata or {},
        )
        self._session.add(ws)
        await self._session.flush()
        return ws

    async def list_workstreams(self, mission_id: str) -> list[OrgWorkstream]:
        result = await self._session.execute(
            select(OrgWorkstream)
            .where(
                and_(
                    OrgWorkstream.tenant_id == self._tenant_id,
                    OrgWorkstream.mission_id == uuid.UUID(mission_id),
                )
            )
            .order_by(OrgWorkstream.order_index)
        )
        return list(result.scalars().all())

    # ── WS-2b: Mission decomposition → real subtasks → handoff → deliverable ──
    #
    # A mission is dispatched to the agent loop as a real goal (the fine-grained
    # plan→execute→verify happens there). ALONGSIDE that, the org layer records a
    # real decomposition: one OrgTask per subtask, assigned to the formed team,
    # with task.decomposed / task.assigned / agent.working / task.handoff /
    # mission.progress events so the console animates genuine activity, and a
    # finalize step that reconciles the subtasks against the real goal outcome
    # and aggregates a real deliverable/report.

    # Deterministic phase templates for the no-LLM / single-department fallback.
    _DECOMP_PHASES: ClassVar[list[tuple[str, str]]] = [
        ("Research & context", "Gather the context, constraints and information needed for: {obj}"),
        ("Execute core work", "Carry out the core work required to accomplish: {obj}"),
        ("Verify & deliver", "Verify the result and compile the final deliverable for: {obj}"),
    ]

    async def decompose_mission(
        self,
        *,
        objective: str,
        title: str = "",
        team_departments: list[str] | None = None,
        llm_provider: Any | None = None,
        max_subtasks: int = 6,
    ) -> list[dict[str, Any]]:
        """Break a mission objective into ordered, real subtasks.

        LLM-driven when a provider is supplied (and returns a usable plan);
        otherwise degrades to a deterministic decomposition derived from the
        formed team's departments (one subtask per department) or, for a
        single-department team, a 3-phase research→execute→verify plan. Always
        returns at least two subtasks for a non-trivial objective.
        """
        obj = " ".join((objective or title).strip().split()) or "the mission objective"

        # 1. LLM-driven decomposition (best effort).
        if llm_provider is not None:
            try:
                llm_subtasks = await self._decompose_mission_llm(obj, llm_provider, max_subtasks)
                if len(llm_subtasks) >= 2:
                    return [
                        {**st, "order": i} for i, st in enumerate(llm_subtasks[:max_subtasks])
                    ]
            except Exception as exc:  # pragma: no cover - provider variance
                _log.warning("org.decompose_mission.llm_failed", error=str(exc)[:120])

        # 2. Deterministic fallback.
        depts = [d for d in dict.fromkeys(team_departments or []) if d]
        subtasks: list[dict[str, Any]] = []
        if len(depts) >= 2:
            for dept in depts[:max_subtasks]:
                label = str(dept).replace("_", " ").title()
                subtasks.append(
                    {
                        "title": f"{label} workstream",
                        "objective": f"{label} contribution to: {obj}",
                        "department": dept,
                        "capabilities": [],
                    }
                )
        else:
            only_dept = depts[0] if depts else None
            for name, tmpl in self._DECOMP_PHASES:
                subtasks.append(
                    {
                        "title": name,
                        "objective": tmpl.format(obj=obj),
                        "department": only_dept,
                        "capabilities": [],
                    }
                )
        return [{**st, "order": i} for i, st in enumerate(subtasks)]

    async def _decompose_mission_llm(
        self, objective: str, llm_provider: Any, max_subtasks: int
    ) -> list[dict[str, Any]]:
        """Ask the LLM for an ordered subtask plan. Returns [] on parse failure."""
        import json

        from app.providers.base import CompletionRequest, Message

        prompt = (
            "Decompose this mission objective into 2-"
            f"{max_subtasks} ordered, concrete subtasks that different team "
            "members could own. Respond ONLY with a JSON array of objects, each "
            '{"title": "<short>", "objective": "<what to do>"}.\n\n'
            f"OBJECTIVE: {objective}"
        )
        req = CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            model=getattr(llm_provider, "_default_model", "") or "claude-sonnet-4-5",
            max_tokens=600,
        )
        resp = await llm_provider.complete(req)
        raw = (resp.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict) and item.get("title"):
                out.append(
                    {
                        "title": str(item["title"])[:200],
                        "objective": str(item.get("objective") or item["title"])[:1000],
                        "department": item.get("department"),
                        "capabilities": list(item.get("capabilities") or []),
                    }
                )
        return out

    async def decompose_and_assign(
        self,
        *,
        mission: Any,
        org_id: str,
        objective: str,
        title: str,
        team_id: str | None,
        team_departments: list[str] | None = None,
        viz_agent_ids: list[str] | None = None,
        llm_provider: Any | None = None,
    ) -> list[OrgTask]:
        """Create real OrgTask subtasks for a mission and emit the rich events.

        Emits: ``task.decomposed`` (once), then per subtask ``task.assigned`` +
        ``agent.working``, ``task.handoff`` between consecutive subtasks owned by
        different agents, and a final ``mission.progress`` (0%).
        """
        subtasks = await self.decompose_mission(
            objective=objective,
            title=title,
            team_departments=team_departments,
            llm_provider=llm_provider,
        )
        org_uuid = uuid.UUID(str(mission.org_id))
        mission_id = str(mission.id)
        viz_agent_ids = list(viz_agent_ids or [])
        n_agents = len(viz_agent_ids) or 1

        await self._emit_event(
            org_uuid,
            "task.decomposed",
            title=f"Mission '{title}' decomposed into {len(subtasks)} subtasks",
            entity_type="mission",
            entity_id=mission_id,
            payload={
                "mission_id": mission_id,
                "subtasks": [s["title"] for s in subtasks],
                "count": len(subtasks),
            },
            source="orchestrator",
        )

        tasks: list[OrgTask] = []
        prev_agent: str | None = None
        prev_task_id: str | None = None
        prev_dept: str | None = None
        for idx, st in enumerate(subtasks):
            task = await self.create_task(
                org_id=org_id,
                mission_id=mission_id,
                title=str(st["title"]),
                objective=str(st.get("objective") or st["title"]),
                required_capabilities=list(st.get("capabilities") or []),
                assigned_team_id=team_id,
                depth=1,
                metadata={
                    "subtask_index": idx,
                    "department": st.get("department"),
                    "task_kind": "subtask",
                },
            )
            await self.update_task_status(str(task.id), "assigned")
            agent_id = (
                viz_agent_ids[idx % n_agents]
                if viz_agent_ids
                else f"{mission_id[:8]}-agent-{idx + 1}"
            )
            await self._emit_event(
                org_uuid,
                "task.assigned",
                title=f"'{st['title']}' assigned",
                entity_type="task",
                entity_id=str(task.id),
                payload={
                    "task_id": str(task.id),
                    "mission_id": mission_id,
                    "agent_id": agent_id,
                    "team_id": team_id,
                    "order": idx,
                    "department": st.get("department"),
                },
                source="orchestrator",
            )
            await self._emit_event(
                org_uuid,
                "agent.working",
                title=f"Agent {agent_id} working on '{st['title']}'",
                entity_type="agent",
                entity_id=agent_id,
                payload={
                    "agent_id": agent_id,
                    "task_id": str(task.id),
                    "mission_id": mission_id,
                    "title": st["title"],
                },
                source="orchestrator",
            )
            # A decomposed mission is a pipeline: each subtask hands its result
            # to the next. Emit a handoff for every consecutive pair, carrying the
            # real owner/department transition (which may or may not change).
            cur_dept = st.get("department")
            if prev_task_id is not None:
                await self._emit_event(
                    org_uuid,
                    "task.handoff",
                    title=f"Handoff {prev_agent} → {agent_id}",
                    entity_type="task",
                    entity_id=str(task.id),
                    payload={
                        "from_agent": prev_agent,
                        "to_agent": agent_id,
                        "from_task": prev_task_id,
                        "to_task": str(task.id),
                        "from_department": prev_dept,
                        "to_department": cur_dept,
                        "mission_id": mission_id,
                    },
                    source="orchestrator",
                )
            prev_agent = agent_id
            prev_task_id = str(task.id)
            prev_dept = cur_dept
            tasks.append(task)

        await self._emit_event(
            org_uuid,
            "mission.progress",
            title=f"Mission '{title}' dispatched",
            entity_type="mission",
            entity_id=mission_id,
            payload={
                "mission_id": mission_id,
                "progress": 0.0,
                "subtasks_total": len(tasks),
                "subtasks_done": 0,
                "phase": "dispatched",
            },
            source="orchestrator",
        )
        return tasks

    async def reassign_task(
        self,
        *,
        task_id: str,
        to_team_id: str | None = None,
        to_agent_id: str | None = None,
        reason: str = "",
    ) -> OrgTask | None:
        """Reassign a task to a different team/agent and emit ``task.handoff``."""
        task = await self.get_task(task_id)
        if not task:
            return None
        from_team = str(task.assigned_team_id) if task.assigned_team_id else None
        from_agent = (list(task.assigned_agent_ids or []) or [None])[0]
        if to_team_id:
            task.assigned_team_id = uuid.UUID(to_team_id)
        if to_agent_id:
            task.assigned_agent_ids = [to_agent_id]
        task.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._emit_event(
            cast(uuid.UUID, task.org_id),
            "task.handoff",
            title=f"Task '{task.title}' handed off",
            entity_type="task",
            entity_id=task_id,
            payload={
                "task_id": task_id,
                "from_team": from_team,
                "to_team": to_team_id,
                "from_agent": from_agent,
                "to_agent": to_agent_id,
                "reason": reason,
            },
            source="orchestrator",
        )
        return task

    async def _disband_team_if_idle(
        self, team_id: str | uuid.UUID | None, org_id: uuid.UUID
    ) -> None:
        """Disband a mission's team once no non-terminal mission still needs it.

        Teams are formed per mission (1:1) and were never torn down, so the
        dashboard's 'Active Teams' count kept climbing while active missions
        dropped to zero. When a mission finalizes we disband its team — unless
        another still-running mission is assigned to the same team (defensive:
        teams may be shared in future) — and emit ``org.team.disbanded`` so
        dashboards and the activity feed track the change.
        """
        if not team_id:
            return
        team = (
            await self._session.execute(
                select(OrgTeam).where(
                    OrgTeam.id == team_id,
                    OrgTeam.tenant_id == self._tenant_id,
                )
            )
        ).scalar_one_or_none()
        if team is None or team.status != "active":
            return
        # Is any OTHER non-terminal mission still assigned to this team?
        still_in_use = (
            await self._session.execute(
                select(func.count(OrgMission.id)).where(
                    OrgMission.tenant_id == self._tenant_id,
                    OrgMission.assigned_team_id == team_id,
                    OrgMission.status.not_in(
                        ("completed", "failed", "cancelled", "archived")
                    ),
                )
            )
        ).scalar() or 0
        if still_in_use > 0:
            return
        team.status = "disbanded"
        team.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._emit_event(
            org_id,
            "org.team.disbanded",
            title=f"Team '{team.name}' disbanded",
            entity_type="team",
            entity_id=str(team.id),
            payload={"team_id": str(team.id), "reason": "mission_finalized"},
            source="orchestrator",
        )

    async def dispatch_mission_goal(
        self,
        mission_id: str,
        *,
        app_state: Any = None,
        tenant_ctx: Any | None = None,
    ) -> dict[str, Any]:
        """Launch the goal for a mission that was paused awaiting approval.

        ``create_mission_and_execute`` defers dispatch when a mission has approval
        gates — it stores the dispatch params under ``extra_data['pending_dispatch']``
        and leaves the mission in ``review``. Once every gate is approved this
        submits the goal and activates the mission, so an approval gate is a HARD
        stop, not a passive sign-off. No-op if already dispatched or not paused.
        """
        mission = await self.get_mission(mission_id)
        if mission is None:
            return {"error": "mission_not_found", "dispatched": False}
        meta = dict(mission.extra_data or {})
        if meta.get("goal_id"):
            return {"already_dispatched": True, "goal_id": meta["goal_id"], "dispatched": False}
        goal_service = getattr(app_state, "goal_service", None)
        if goal_service is None:
            return {"error": "goal_service_unavailable", "dispatched": False}
        if tenant_ctx is None:
            from app.tenancy.context import PlanTier, TenantContext

            tenant_ctx = TenantContext(
                tenant_id=self._tenant_id,
                plan=PlanTier.PROFESSIONAL,
                api_key_id="org_gate_dispatch",
            )
        pd = meta.get("pending_dispatch") or {}
        execution_ctx = pd.get("execution_context") or {
            "org_id": str(mission.org_id),
            "mission_id": mission_id,
            "source": "org_mission",
        }
        try:
            goal_result = await goal_service.submit_goal(
                goal=mission.objective or mission.title,
                priority=str(pd.get("priority") or mission.priority or "normal"),
                dry_run=False,
                tenant_ctx=tenant_ctx,
                workflow_mode=pd.get("workflow_mode", "single_agent"),
                execution_context=execution_ctx,
            )
        except Exception as exc:
            _log.error(
                "org.dispatch_mission_goal.submit_failed",
                mission_id=mission_id,
                error=str(exc)[:200],
            )
            return {"error": str(exc)[:200], "dispatched": False}
        goal_id = goal_result.get("goal_id")
        meta["goal_id"] = goal_id
        meta.pop("pending_dispatch", None)
        mission.extra_data = meta
        mission.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self.update_mission_status(mission_id, "active")
        _log.info("org.dispatch_mission_goal.dispatched", mission_id=mission_id, goal_id=goal_id)
        return {"goal_id": goal_id, "dispatched": True}

    async def finalize_mission(
        self,
        mission_id: str,
        *,
        app_state: Any = None,
        tenant_ctx: Any | None = None,
    ) -> dict[str, Any]:
        """Reconcile a mission's subtasks against its real goal outcome.

        Reads the dispatched goal's terminal status from the wired GoalService,
        marks the org subtasks completed/failed to match, aggregates a real
        deliverable/report onto the mission, and transitions the mission —
        emitting ``mission.progress`` (100%) and (via update_mission_status)
        ``mission.completed`` / ``mission.failed``. When the goal is not yet
        terminal it emits partial progress and returns ``finalized=False``.
        """
        mission = await self.get_mission(mission_id)
        if not mission:
            return {"error": "mission_not_found", "finalized": False}
        org_uuid = cast(uuid.UUID, mission.org_id)

        goal_id = (mission.extra_data or {}).get("goal_id")
        goal_status: str | None = None
        goal_result: Any = None
        goal_service = getattr(app_state, "goal_service", None)
        if goal_id and goal_service is not None:
            try:
                if tenant_ctx is None:
                    from app.tenancy.context import PlanTier, TenantContext

                    tenant_ctx = TenantContext(
                        tenant_id=self._tenant_id,
                        plan=PlanTier.PROFESSIONAL,
                        api_key_id="org_mission_finalize",
                    )
                goal = await goal_service.get_goal(str(goal_id), tenant_ctx)
                goal_status = str(goal.get("status"))
                goal_result = goal.get("result_artifact")
            except Exception as exc:
                _log.warning(
                    "org.finalize_mission.goal_read_failed",
                    mission_id=mission_id,
                    error=str(exc)[:120],
                )

        all_tasks = await self.list_tasks(str(org_uuid), mission_id=mission_id, limit=200)
        subtasks = [
            t for t in all_tasks if (t.extra_data or {}).get("task_kind") == "subtask"
        ] or all_tasks

        terminal_ok = goal_status in ("complete", "completed", "succeeded", "success")
        terminal_fail = goal_status in ("failed", "error", "cancelled")

        if not (terminal_ok or terminal_fail):
            done = sum(1 for t in subtasks if t.status == "completed")
            total = len(subtasks) or 1
            await self._emit_event(
                org_uuid,
                "mission.progress",
                title=f"Mission '{mission.title}' in progress",
                entity_type="mission",
                entity_id=mission_id,
                payload={
                    "mission_id": mission_id,
                    "progress": round(done / total, 3),
                    "subtasks_total": len(subtasks),
                    "subtasks_done": done,
                    "phase": "running",
                    "goal_status": goal_status,
                },
                source="orchestrator",
            )
            return {
                "mission_id": mission_id,
                "status": str(mission.status),
                "goal_status": goal_status,
                "finalized": False,
            }

        new_task_status = "completed" if terminal_ok else "failed"
        # Close EVERY non-terminal task on the mission, not only 'subtask'-kind ones.
        # Approval-gate tasks (status 'approval_required') were being left behind, so
        # a finished mission kept inflating pending-approval counts with a gate that
        # has no live HITL request and can never be actioned from the inbox.
        for t in all_tasks:
            if t.status not in ("completed", "failed", "cancelled", "expired"):
                await self.update_task_status(str(t.id), new_task_status)

        report: dict[str, Any] = {
            "objective": mission.objective or mission.title,
            "goal_id": goal_id,
            "goal_status": goal_status,
            "subtasks": [{"title": t.title, "status": new_task_status} for t in subtasks],
            "deliverable": goal_result,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        mission.outputs = [*list(mission.outputs or []), report]
        new_meta = dict(mission.extra_data or {})
        new_meta["result"] = report
        mission.extra_data = new_meta
        mission.updated_at = datetime.now(UTC)
        await self._session.flush()

        final_status = "completed" if terminal_ok else "failed"
        # update_mission_status emits mission.completed / mission.failed.
        await self.update_mission_status(mission_id, final_status)
        # Team lifecycle: disband the mission's team so 'Active Teams' tracks
        # active missions instead of climbing forever.
        await self._disband_team_if_idle(
            cast("uuid.UUID | None", mission.assigned_team_id), org_uuid
        )
        await self._emit_event(
            org_uuid,
            "mission.progress",
            title=f"Mission '{mission.title}' finalized",
            entity_type="mission",
            entity_id=mission_id,
            payload={
                "mission_id": mission_id,
                "progress": 1.0,
                "subtasks_total": len(subtasks),
                "subtasks_done": len(subtasks) if terminal_ok else 0,
                "phase": "completed" if terminal_ok else "failed",
                "result": report,
            },
            source="orchestrator",
        )
        return {
            "mission_id": mission_id,
            "status": final_status,
            "goal_status": goal_status,
            "result": report,
            "finalized": True,
        }

    # ── Integration Point 1: Mission → Agent Execution Bridge ────────────────
    #
    # This is the critical wiring that connects the Org OS layer to the existing
    # AgentGraph execution engine.  Every mission submitted here goes through:
    #   OrgService.create_mission_and_execute()
    #     → MetaOrchestrator.plan_mission()        (team formation + topology)
    #     → GoalService.submit_goal()              (Celery dispatch → AgentGraph)
    #     → mission.extra_data["goal_id"] updated  (linkage preserved in DB)

    async def create_mission_and_execute(
        self,
        *,
        org_id: str,
        title: str,
        objective: str = "",
        why: str = "",
        expected_outcome: str = "",
        priority: str = "medium",
        dept_id: str | None = None,
        assigned_team_id: str | None = None,
        autonomy_level: int | None = None,
        source: str = "api",
        success_criteria: list[Any] | None = None,
        budget_usd: float | None = None,
        deadline: datetime | None = None,
        tags: list[str] | None = None,
        created_by: str | None = None,
        metadata: dict[str, Any] | None = None,
        # Optional caller-supplied TenantContext (richer plan tier).
        # When omitted a PROFESSIONAL-tier context is synthesised from tenant_id.
        tenant_ctx: Any | None = None,
        # The request's app.state (lifespan-wired services). Threaded by the API
        # caller so this uses the DB/Redis-backed GoalService / LLM provider
        # instead of the module-level app.main.app singleton's unwired fallbacks.
        app_state: Any = None,
    ) -> tuple[OrgMission, dict[str, Any]]:
        """Create an OrgMission and immediately dispatch it to the AgentGraph.

        This is the single entry-point for fully autonomous mission execution:

        1. Persist the OrgMission record.
        2. Run MetaOrchestrator to produce an OrchestrationPlan (team + topology).
        3. Dispatch to GoalService → Celery → AgentGraph.run() with org context
           injected so the agent knows its dept, role, and mission.
        4. Store the resulting goal_id back on the mission for status tracking.
        5. Update mission status → "active" and emit telemetry.

        Returns ``(mission, dispatch_result)`` where dispatch_result contains
        goal_id, orchestration plan summary, and topology chosen.
        """
        with _tracer.start_as_current_span("org.create_mission_and_execute") as span:
            span.set_attribute("tenant_id", self._tenant_id)
            span.set_attribute("org_id", org_id)
            span.set_attribute("mission.priority", priority)
            span.set_attribute("mission.source", source)

            # ── Step 1: Persist the mission record ───────────────────────────
            mission = await self.create_mission(
                org_id=org_id,
                title=title,
                objective=objective,
                why=why,
                expected_outcome=expected_outcome,
                priority=priority,
                dept_id=dept_id,
                assigned_team_id=assigned_team_id,
                autonomy_level=autonomy_level,
                source=source,
                success_criteria=success_criteria,
                budget_usd=budget_usd,
                deadline=deadline,
                tags=tags,
                created_by=created_by,
                metadata=metadata,
            )

            # Reads as "Planned" (queued for execution) the instant the mission
            # appears in the UI over SSE — nicer than the default "draft" while
            # the team forms. form_team_and_dispatch flips it to active/review.
            await self.update_mission_status(str(mission.id), "planned")

            dispatch_result = await self.form_team_and_dispatch(
                mission=mission,
                org_id=org_id,
                objective=objective,
                title=title,
                expected_outcome=expected_outcome,
                dept_id=dept_id,
                assigned_team_id=assigned_team_id,
                autonomy_level=autonomy_level,
                priority=priority,
                tenant_ctx=tenant_ctx,
                app_state=app_state,
            )
            span.set_attribute("dispatched", dispatch_result.get("goal_id") is not None)
            return mission, dispatch_result

    async def form_team_and_dispatch(
        self,
        *,
        mission: OrgMission,
        org_id: str,
        objective: str = "",
        title: str = "",
        expected_outcome: str = "",
        dept_id: str | None = None,
        assigned_team_id: str | None = None,
        autonomy_level: int | None = None,
        priority: str = "medium",
        tenant_ctx: Any | None = None,
        app_state: Any = None,
    ) -> dict[str, Any]:
        """Form the team (MetaOrchestrator), decompose into tasks, and dispatch
        the goal to the AgentGraph.

        Split out of ``create_mission_and_execute`` so the API can persist and
        return the mission row immediately, then run this — the slow part (LLM
        team formation + decomposition) — in the background.
        """
        with _tracer.start_as_current_span("org.form_team_and_dispatch") as span:
            span.set_attribute("tenant_id", self._tenant_id)
            span.set_attribute("org_id", org_id)

            # ── Step 2: Load org entity for MetaOrchestrator ─────────────────
            org = await self.get_organization(org_id)
            if org is None:
                _log.warning("org.create_mission_and_execute.org_not_found", org_id=org_id)
                return {"goal_id": None, "error": "org_not_found"}

            # ── Step 3: Form the team + produce execution plan ────────────────
            dispatch_result: dict[str, Any] = {
                "mission_id": str(mission.id),
                "goal_id": None,
                "topology": "sequential",
                "departments": [],
                "autonomy_level": autonomy_level or 2,
                "estimated_cost_usd": 0.0,
            }

            try:
                from app.org.meta_orchestrator import MetaOrchestrator

                # Resolve LLM provider from the request's wired app.state.
                # (Reads _app_provider — the attribute create_app actually binds —
                # so mission decomposition is genuinely LLM-driven, not always the
                # heuristic fallback.)
                _llm_provider: Any | None = None
                with contextlib.suppress(Exception):
                    _llm_provider = resolve_llm_provider(app_state)

                orchestrator = MetaOrchestrator(llm_provider=_llm_provider)
                orch_plan = await orchestrator.plan_mission(
                    objective or title,
                    org,
                    self._tenant_id,
                    mission,
                )

                dispatch_result.update(
                    {
                        "topology": orch_plan.topology,
                        "departments": orch_plan.departments,
                        "autonomy_level": orch_plan.autonomy_level,
                        "estimated_cost_usd": orch_plan.estimated_total_cost_usd,
                        "estimated_duration_hours": orch_plan.estimated_total_duration_hours,
                        "agent_count": orch_plan.team_manifest.agent_count
                        if orch_plan.team_manifest
                        else 0,
                        "requires_preview": getattr(
                            orch_plan.team_manifest, "requires_human_preview", False
                        ),
                    }
                )

                if orch_plan.team_manifest is not None:
                    team_manifest = orch_plan.team_manifest
                    agent_ids = [f"agent-{idx + 1}" for idx, _ in enumerate(team_manifest.roles)]
                    dispatch_result["agent_ids"] = agent_ids

                    resolved_team_id = assigned_team_id
                    if not resolved_team_id:
                        materialized_team = await self.create_team(
                            org_id=org_id,
                            name=team_manifest.team_name,
                            purpose=objective or expected_outcome or title,
                            dept_id=dept_id,
                            team_type="mission",
                            member_agent_ids=agent_ids,
                            metadata={
                                "mission_id": str(mission.id),
                                "formation_reasoning": team_manifest.formation_reasoning,
                            },
                        )
                        resolved_team_id = str(materialized_team.id)

                    mission.assigned_team_id = uuid.UUID(resolved_team_id)
                    dispatch_result["team_id"] = resolved_team_id

                    # Spawn a live node in the agent constellation for each team
                    # member (org.agent.activated → the neural-state bridge), and
                    # announce the formed team with its roster so the graph can draw
                    # communication beams between members.
                    roles = list(getattr(team_manifest, "roles", []) or [])
                    mission_slug = str(mission.id)[:8]
                    # Distinct node id per (mission, member) so each mission's squad
                    # shows as its own agents in the constellation rather than
                    # collapsing onto the generic agent-1/agent-2 ids.
                    viz_agent_ids = [f"{mission_slug}-{aid}" for aid in agent_ids]
                    for idx, viz_agent_id in enumerate(viz_agent_ids):
                        role_label = ""
                        if idx < len(roles):
                            role_obj = roles[idx]
                            role_label = str(
                                getattr(role_obj, "title", None)
                                or getattr(role_obj, "name", None)
                                or getattr(role_obj, "role", None)
                                or role_obj
                            )[:60]
                        await self._emit_event(
                            uuid.UUID(str(mission.org_id)),
                            "agent.activated",
                            title=f"Agent {viz_agent_id} joined the team",
                            entity_type="agent",
                            entity_id=viz_agent_id,
                            payload={
                                "agent_id": viz_agent_id,
                                "team_id": resolved_team_id,
                                "mission_id": str(mission.id),
                                "role": role_label or f"Agent {idx + 1}",
                            },
                            source="orchestrator",
                        )
                    await self._emit_event(
                        uuid.UUID(str(mission.org_id)),
                        "team.formed",
                        title=f"Team formed for '{mission.title}'",
                        entity_type="team",
                        entity_id=resolved_team_id,
                        payload={
                            "team_id": resolved_team_id,
                            "mission_id": str(mission.id),
                            "agent_ids": viz_agent_ids,
                            "topology": orch_plan.topology,
                        },
                        source="orchestrator",
                    )

                    # WS-2b: real decomposition into org subtasks assigned to the
                    # formed team, with task.decomposed / task.assigned /
                    # agent.working / task.handoff / mission.progress events.
                    try:
                        await self.decompose_and_assign(
                            mission=mission,
                            org_id=org_id,
                            objective=objective or title,
                            title=title,
                            team_id=resolved_team_id,
                            team_departments=list(orch_plan.departments or []),
                            viz_agent_ids=viz_agent_ids,
                            llm_provider=_llm_provider,
                        )
                    except Exception as decomp_exc:
                        _log.warning(
                            "org.create_mission_and_execute.decomposition_failed",
                            mission_id=str(mission.id),
                            error=str(decomp_exc)[:120],
                        )

                span.set_attribute("topology", orch_plan.topology)
                span.set_attribute("dept_count", len(orch_plan.departments))

            except Exception as orch_exc:
                _log.warning(
                    "org.create_mission_and_execute.orchestration_fallback",
                    mission_id=str(mission.id),
                    error=str(orch_exc)[:120],
                )
                orch_plan = None  # type: ignore[assignment]

            # ── Step 4: Dispatch to GoalService → AgentGraph (Celery) ─────────
            try:
                goal_service = getattr(app_state, "goal_service", None)
            except Exception:
                goal_service = None

            if goal_service is not None:
                # Build a minimal TenantContext when caller didn't provide one
                if tenant_ctx is None:
                    from app.tenancy.context import PlanTier, TenantContext

                    tenant_ctx = TenantContext(
                        tenant_id=self._tenant_id,
                        plan=PlanTier.PROFESSIONAL,
                        api_key_id="org_mission_dispatch",
                    )

                # Serialise orch_plan summary for injection into agent context
                plan_summary: dict[str, Any] = {
                    "topology": dispatch_result.get("topology", "sequential"),
                    "departments": dispatch_result.get("departments", []),
                    "autonomy_level": dispatch_result.get("autonomy_level", 2),
                }
                if orch_plan is not None:
                    try:
                        plan_summary["approval_gates"] = orch_plan.approval_gates
                        plan_summary["model_profile"] = str(orch_plan.model_gateway_profile)
                    except Exception:
                        pass

                # G-21: Check if approval gates require pre-dispatch approval.
                # G-06: Wire ApprovalChainEngine into the execution path.
                # G-29: Notify via NotificationService + publish org.approval.requested event.
                approval_gates = plan_summary.get("approval_gates", [])
                if approval_gates:
                    try:
                        from app.org.approval_chain import (
                            get_approval_engine,
                        )
                        from app.org.events import get_org_event_publisher

                        _engine = get_approval_engine()
                        _publisher = get_org_event_publisher()
                        # WS-3 fix: this referenced an undefined ``_app`` name,
                        # which raised NameError on every single mission that
                        # computed any approval_gates — silently swallowed by
                        # the broad except below as "approval_gate_wiring_failed"
                        # and skipping task/approval-request/notification
                        # creation *and* the supervised-autonomy override below.
                        _notif = getattr(app_state, "notification_service", None)

                        for gate in (
                            approval_gates[:3] if isinstance(approval_gates, list) else []
                        ):
                            gate_title = (
                                str(gate)
                                if isinstance(gate, str)
                                else str(gate.get("type", "approval"))
                            )
                            # G-06: Ask ApprovalChainEngine whether this gate matches a chain.
                            _chain: ApprovalChain | None = None
                            _req = None
                            try:
                                _chain = await _engine.check_requires_approval(
                                    gate_title,
                                    {"org_id": org_id, "mission_id": str(mission.id)},
                                    org,
                                )
                            except Exception as _ce_exc:
                                _chain = None
                                _log.warning(
                                    "org.approval_chain_check_failed",
                                    error=str(_ce_exc)[:100],
                                )

                            task = await self.create_task(
                                org_id=org_id,
                                mission_id=str(mission.id),
                                title=f"Approval gate: {gate_title}",
                                objective=f"This mission requires approval for: {gate_title}",
                                why=gate_title,
                                risk_level=getattr(_chain, "risk_threshold", "high")
                                if _chain
                                else "high",
                                metadata={
                                    "gate": gate if isinstance(gate, dict) else {"type": gate_title},  # noqa: E501
                                    "task_kind": "approval_gate",
                                },
                            )
                            # G-22: Set task status to approval_required
                            await self.update_task_status(str(task.id), "approval_required")

                            # WS-3b: register a paired HITLGateway request so the
                            # org task-level approve/reject endpoint resolves the
                            # SAME gateway a blocked agent waits on (one gateway,
                            # no parallel mechanism). Store its id on the task.
                            _gateway = getattr(app_state, "hitl_gateway", None)
                            if _gateway is not None:
                                try:
                                    _hitl_req = _gateway.request_approval(
                                        goal_id=str(mission.id),
                                        action=f"Org approval gate: {gate_title}",
                                        risk_level=getattr(_chain, "risk_threshold", "high")
                                        if _chain
                                        else "high",
                                        tenant_ctx=tenant_ctx,
                                        context={
                                            "org_id": org_id,
                                            "mission_id": str(mission.id),
                                            "task_id": str(task.id),
                                            "gate": gate_title,
                                        },
                                    )
                                    _hitl_rid = getattr(
                                        _hitl_req, "request_id", str(_hitl_req)
                                    )
                                    await self.update_task_status(
                                        str(task.id),
                                        "approval_required",
                                        outputs=[{"hitl_request_id": _hitl_rid}],
                                    )
                                except Exception as _hg_exc:
                                    _log.warning(
                                        "org.approval_gate_hitl_register_failed",
                                        error=str(_hg_exc)[:100],
                                    )

                            # Record req id on the task for the approve/reject path.
                            if _chain is not None:
                                try:
                                    _req = await _engine.create_approval_request(
                                        chain=_chain,
                                        action_detail=gate_title,
                                        mission_id=str(mission.id),
                                        agent_id=None,
                                        tenant_id=self._tenant_id,
                                        org_id=org_id,
                                    )
                                    await self.update_task_status(
                                        str(task.id),
                                        "approval_required",
                                        outputs=[
                                            {
                                                "approval_request_id": _req.request_id,
                                                "chain_id": _chain.id,
                                            }
                                        ],
                                    )
                                except Exception as _ar_exc:
                                    _log.warning(
                                        "org.approval_request_create_failed",
                                        error=str(_ar_exc)[:100],
                                    )

                            # G-19/G-29: Publish org.approval.requested event + notify.
                            try:
                                await _publisher.publish(
                                    event_type="org.approval.requested",
                                    org_id=org_id,
                                    tenant_id=self._tenant_id,
                                    payload={
                                        "mission_id": str(mission.id),
                                        "task_id": str(task.id),
                                        "gate": gate_title,
                                        "risk": getattr(_chain, "risk_threshold", "high")
                                        if _chain
                                        else "high",
                                    },
                                )
                            except Exception as _ev_exc:
                                _log.warning(
                                    "org.approval_event_publish_failed",
                                    error=str(_ev_exc)[:100],
                                )

                            if _notif is not None:
                                try:
                                    await _notif.notify_approval_required(
                                        request_id=getattr(_req, "request_id", str(task.id))
                                        if _chain
                                        else str(task.id),
                                        goal_id=str(mission.id),
                                        action=gate_title,
                                        risk_level=getattr(_chain, "risk_threshold", "high")
                                        if _chain
                                        else "high",
                                        tenant_id=self._tenant_id,
                                    )
                                except Exception as _n_exc:
                                    _log.warning(
                                        "org.approval_notify_failed",
                                        error=str(_n_exc)[:100],
                                    )

                            span.set_attribute("approval_gates", gate_title)
                    except Exception as _gate_outer:
                        _log.warning(
                            "org.approval_gate_wiring_failed",
                            error=str(_gate_outer)[:120],
                        )

                # Execution context injected into every AgentGraph call:
                # The graph's run() reads these as initial_context / org attributes.
                execution_ctx: dict[str, Any] = {
                    "org_id": org_id,
                    "mission_id": str(mission.id),
                    "mission_title": title,
                    "source": "org_mission",
                    "orchestration_plan": plan_summary,
                }
                if dept_id:
                    execution_ctx["dept_id"] = dept_id
                if assigned_team_id:
                    execution_ctx["team_id"] = assigned_team_id
                if approval_gates:
                    # WS-3: MetaOrchestrator already flagged this mission as
                    # needing human oversight (high/critical risk, legal/finance
                    # involvement, or over the cost threshold — see
                    # _compute_approval_gates). Force the dispatched goal into
                    # "supervised" autonomy so its agent actually BLOCKS on a
                    # gated action via the shared HITLGateway (bounded-autonomous,
                    # the default, only logs and proceeds) instead of relying on
                    # whichever agent auto-routing happens to pick.
                    execution_ctx["autonomy_mode"] = "supervised"

                workflow_mode = dispatch_result.get("topology", "sequential")
                # Map org topologies → GoalService workflow modes
                _topology_mode_map = {
                    "sequential": "single_agent",
                    "parallel": "multi_agent",
                    "hierarchical": "multi_agent",
                    "swarm": "multi_agent",
                    "pipeline": "single_agent",
                    "moa": "multi_agent",
                }
                workflow_mode = _topology_mode_map.get(workflow_mode, "single_agent")

                # Approval-gated missions are STILL dispatched — but in SUPERVISED
                # autonomy (execution_ctx.autonomy_mode set above). The goal's agent
                # blocks on the gated high-risk step via the shared HITLGateway,
                # which the org/goal approve endpoints resolve. Previously a gated
                # mission was paused pre-dispatch with NO goal at all, which
                # stranded it (no goal_id to track, mission stuck in 'review') and
                # contradicted the supervised-goal contract the HITL flow and the
                # frontend expect: a dispatched mission must always yield a goal_id.
                try:
                    goal_result = await goal_service.submit_goal(
                        goal=objective or title,
                        priority=priority,
                        dry_run=False,
                        tenant_ctx=tenant_ctx,
                        workflow_mode=workflow_mode,
                        execution_context=execution_ctx,
                    )
                    goal_id_val: str | None = goal_result.get("goal_id")
                    dispatch_result["goal_id"] = goal_id_val
                    dispatch_result["goal_status"] = goal_result.get("status", "queued")
                    span.set_attribute("goal_id", goal_id_val or "")
                    _log.info(
                        "org.create_mission_and_execute.dispatched",
                        mission_id=str(mission.id),
                        goal_id=goal_id_val,
                        supervised=bool(approval_gates),
                        topology=workflow_mode,
                    )
                except Exception as submit_exc:
                    _log.error(
                        "org.create_mission_and_execute.submit_failed",
                        mission_id=str(mission.id),
                        error=str(submit_exc)[:200],
                    )
                    dispatch_result["error"] = str(submit_exc)[:200]
            else:
                _log.warning(
                    "org.create_mission_and_execute.no_goal_service",
                    mission_id=str(mission.id),
                )
                dispatch_result["warning"] = "goal_service_unavailable"

            # ── Step 5: Write goal_id back to mission + activate it ───────────
            goal_id = dispatch_result.get("goal_id")
            if goal_id:
                # NOTE: OrgMission has no "metadata" column — that name is the
                # SQLAlchemy declarative Base.metadata (a MetaData registry
                # object). The actual JSONB scratch field is "extra_data";
                # writing to "metadata" silently no-ops on the DB row.
                new_meta = dict(mission.extra_data or {})
                new_meta["goal_id"] = goal_id
                new_meta["orchestration_plan_summary"] = {
                    "topology": dispatch_result.get("topology"),
                    "departments": dispatch_result.get("departments"),
                    "autonomy_level": dispatch_result.get("autonomy_level"),
                    "estimated_cost_usd": dispatch_result.get("estimated_cost_usd"),
                }
                mission.extra_data = new_meta
                mission.updated_at = datetime.now(UTC)
                await self._session.flush()
                # Transition to active — triggers the mission.active event
                await self.update_mission_status(str(mission.id), "active")

            span.set_attribute("dispatched", goal_id is not None)
            return dispatch_result
