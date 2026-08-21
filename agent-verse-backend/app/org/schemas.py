"""Pydantic v2 schemas for the AI Organization OS API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ─── Shared ────────────────────────────────────────────────────────────────────


class _Base(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}


class _BaseResponse(BaseModel):
    model_config = {"from_attributes": True}


class CursorPage[T](BaseModel):
    data: list[T]
    cursor: str | None = None
    hasMore: bool = False  # noqa: N815 — intentional camelCase for API response
    total: int | None = None


# ─── Organization ──────────────────────────────────────────────────────────────


class CreateOrganizationRequest(_Base):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    industry: str = ""
    jurisdiction: str = ""
    mission: str = ""
    vision: str = ""
    autonomy_level: int = Field(default=1, ge=0, le=5)
    risk_tolerance: str = Field(default="medium", pattern="^(low|medium|high)$")
    monthly_budget_usd: float = Field(default=0.0, ge=0)
    blueprint_ids: list[str] = []


class UpdateOrganizationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    mission: str | None = None
    vision: str | None = None
    autonomy_level: int | None = Field(default=None, ge=0, le=5)
    risk_tolerance: str | None = Field(default=None, pattern="^(low|medium|high)$")
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    status: str | None = None


class OrganizationResponse(_BaseResponse):
    id: UUID
    tenant_id: UUID
    name: str
    slug: str
    description: str
    industry: str
    jurisdiction: str
    mission: str
    vision: str
    status: str
    autonomy_level: int
    risk_tolerance: str
    monthly_budget_usd: float
    created_by: str | None
    created_at: datetime
    updated_at: datetime


# ─── Department ───────────────────────────────────────────────────────────────


class CreateDepartmentRequest(_Base):
    org_id: str
    name: str = Field(min_length=1, max_length=200)
    purpose: str = ""
    capability_domains: list[str] = []
    parent_dept_id: str | None = None
    manager_agent_id: str | None = None


class UpdateDepartmentRequest(BaseModel):
    model_config = {"extra": "forbid"}
    name: str | None = None
    purpose: str | None = None
    capability_domains: list[str] | None = None
    manager_agent_id: str | None = None
    status: str | None = None


class DepartmentResponse(_BaseResponse):
    id: UUID
    tenant_id: UUID
    org_id: UUID
    parent_dept_id: UUID | None
    name: str
    purpose: str
    capability_domains: list[str]
    manager_agent_id: str | None
    status: str
    created_at: datetime
    updated_at: datetime


# ─── Mission ──────────────────────────────────────────────────────────────────


class CreateMissionRequest(_Base):
    org_id: str
    title: str = Field(min_length=1, max_length=500)
    objective: str = ""
    why: str = ""
    expected_outcome: str = ""
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    dept_id: str | None = None
    source: str = "manual"
    tags: list[str] = []
    budget_usd: float | None = None
    deadline: datetime | None = None
    autonomy_level: int | None = Field(default=None, ge=0, le=5)
    created_by: str | None = None


class UpdateMissionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    title: str | None = None
    objective: str | None = None
    why: str | None = None
    priority: str | None = Field(default=None, pattern="^(low|medium|high|critical)$")
    status: str | None = None
    assigned_team_id: str | None = None
    budget_usd: float | None = None
    deadline: datetime | None = None


class MissionResponse(_BaseResponse):
    id: UUID
    tenant_id: UUID
    org_id: UUID
    dept_id: UUID | None
    assigned_team_id: UUID | None
    title: str
    objective: str
    why: str
    expected_outcome: str
    status: str
    priority: str
    source: str
    autonomy_level: int | None
    budget_usd: float | None
    deadline: datetime | None
    tags: list[str]
    created_by: str | None
    outputs: list[Any]
    evidence: list[Any]
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MissionStatusUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    status: str


# ─── Task ─────────────────────────────────────────────────────────────────────


class CreateTaskRequest(_Base):
    org_id: str
    title: str = Field(min_length=1, max_length=500)
    objective: str = ""
    mission_id: str | None = None
    parent_task_id: str | None = None
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    risk_level: str = Field(default="low", pattern="^(low|medium|high|critical)$")
    depth: int = Field(default=0, ge=0, le=8)
    assigned_agent_ids: list[str] = []
    required_capabilities: list[str] = []
    required_tools: list[str] = []
    budget_usd: float | None = None
    deadline: datetime | None = None
    dependencies: list[str] = []


class TaskResponse(_BaseResponse):
    id: UUID
    tenant_id: UUID
    org_id: UUID
    mission_id: UUID | None
    parent_task_id: UUID | None
    assigned_team_id: UUID | None
    title: str
    objective: str
    status: str
    priority: str
    depth: int
    risk_level: str
    assigned_agent_ids: list[str]
    dependencies: list[str]
    outputs: list[Any]
    evidence: list[Any]
    budget_usd: float | None
    actual_cost_usd: float | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaskStatusUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    status: str
    outputs: list[Any] | None = None
    evidence: list[Any] | None = None
    actual_cost_usd: float | None = None


# ─── Team ─────────────────────────────────────────────────────────────────────


class CreateTeamRequest(_Base):
    org_id: str
    name: str = Field(min_length=1, max_length=200)
    purpose: str = ""
    dept_id: str | None = None
    team_type: str = "persistent"
    member_agent_ids: list[str] = []
    capability_ids: list[str] = []
    tool_ids: list[str] = []


class TeamResponse(_BaseResponse):
    id: UUID
    tenant_id: UUID
    org_id: UUID
    dept_id: UUID | None
    name: str
    purpose: str
    team_type: str
    manager_agent_id: str | None
    member_agent_ids: list[str]
    capability_ids: list[str]
    status: str
    created_at: datetime
    updated_at: datetime


# ─── Event ────────────────────────────────────────────────────────────────────


class OrgEventResponse(_BaseResponse):
    id: UUID
    org_id: UUID
    event_type: str
    title: str
    description: str
    entity_type: str | None
    entity_id: str | None
    severity: str
    payload: dict[str, Any]
    source: str
    created_at: datetime


# ─── Health ───────────────────────────────────────────────────────────────────


class OrgHealthResponse(BaseModel):
    health: str
    active_missions: int
    active_teams: int
    pending_approvals: int
    task_counts: dict[str, int]
    event_counts_24h: dict[str, int]
    items_needing_attention: int
