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

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from opentelemetry import trace
from sqlalchemy import and_, func, select
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
    OrgTask,
    OrgTeam,
    OrgWorkstream,
)

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

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
        return ev

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
            mission = OrgMission(
                tenant_id=self._tenant_id,
                org_id=uuid.UUID(org_id),
                dept_id=uuid.UUID(dept_id) if dept_id else None,
                title=title,
                objective=objective,
                why=why,
                expected_outcome=expected_outcome,
                priority=priority,
                assigned_team_id=uuid.UUID(assigned_team_id) if assigned_team_id else None,
                autonomy_level=autonomy_level,
                source=source,
                success_criteria=success_criteria or [],
                budget_usd=budget_usd,
                deadline=deadline,
                tags=tags or [],
                created_by=created_by,
                trigger_event=trigger_event,
                metadata=metadata or {},
            )
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

    async def list_missions(
        self,
        org_id: str,
        *,
        status: str | None = None,
        priority: str | None = None,
        dept_id: str | None = None,
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
            await self._emit_event(
                cast(uuid.UUID, mission.org_id),
                f"mission.{status}",
                title=f"Mission '{mission.title}' -> {status}",
                entity_type="mission",
                entity_id=mission_id,
                severity="warning" if status == "failed" else "info",
                payload={"old_status": old_status, "new_status": status},
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
                metadata=metadata or {},
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

            # Pending approvals
            approval_result = await self._session.execute(
                select(func.count(OrgTask.id)).where(
                    and_(
                        OrgTask.tenant_id == self._tenant_id,
                        OrgTask.org_id == uid,
                        OrgTask.status == "approval_required",
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

            # ── Step 2: Load org entity for MetaOrchestrator ─────────────────
            org = await self.get_organization(org_id)
            if org is None:
                _log.warning("org.create_mission_and_execute.org_not_found", org_id=org_id)
                return mission, {"goal_id": None, "error": "org_not_found"}

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
                _llm_provider: Any | None = None
                try:
                    _llm_provider = getattr(app_state, "planner_provider", None)
                except Exception:
                    pass

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
                        _notif = getattr(getattr(_app, "state", None), "notification_service", None)

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

                try:
                    goal_result = await goal_service.submit_goal(
                        goal=objective or title,
                        priority=priority,
                        dry_run=False,
                        tenant_ctx=tenant_ctx,
                        workflow_mode=workflow_mode,
                        execution_context=execution_ctx,
                    )
                    goal_id: str | None = goal_result.get("goal_id")
                    dispatch_result["goal_id"] = goal_id
                    dispatch_result["goal_status"] = goal_result.get("status", "queued")
                    span.set_attribute("goal_id", goal_id or "")
                    _log.info(
                        "org.create_mission_and_execute.dispatched",
                        mission_id=str(mission.id),
                        goal_id=goal_id,
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
            return mission, dispatch_result
