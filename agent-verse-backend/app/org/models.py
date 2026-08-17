"""SQLAlchemy ORM models for the AI Organization Operating System.

All tables are tenant-isolated via RLS. Every model includes the
mandatory: id (UUIDv7), tenant_id, created_at, updated_at.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.sql import func

from app.db.models import Base


def _uuid7() -> uuid.UUID:
    """UUID v7 — time-sortable unique identifier."""
    import time
    ts_ms = int(time.time() * 1000)
    rand = uuid.uuid4().int & ((1 << 80) - 1)
    value = (ts_ms << 80) | (0x7 << 76) | rand
    return uuid.UUID(int=value)


# ── Organization ──────────────────────────────────────────────────────────────

class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        Index("idx_orgs_tenant_id",      "tenant_id"),
        Index("idx_orgs_tenant_status",  "tenant_id", "status"),
        Index("idx_orgs_tenant_created", "tenant_id", "created_at"),
        {"schema": None},
    )

    id          = Column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    tenant_id   = Column(PG_UUID(as_uuid=True), nullable=False)
    name        = Column(String(200), nullable=False)
    slug        = Column(String(64), nullable=False)
    description = Column(Text, default="")
    industry    = Column(String(100), default="")
    jurisdiction = Column(String(100), default="")
    mission     = Column(Text, default="")
    vision      = Column(Text, default="")
    status      = Column(String(50), nullable=False, default="active", index=True)
    autonomy_level       = Column(Integer, nullable=False, default=1)
    risk_tolerance       = Column(String(20), default="medium")
    monthly_budget_usd   = Column(Float, default=0.0)
    goals                = Column(JSONB, default=list)
    policies             = Column(JSONB, default=dict)
    settings             = Column(JSONB, default=dict)
    blueprint_ids        = Column(ARRAY(String), default=list)
    created_by           = Column(String(200), nullable=True)
    extra_data      = Column(JSONB, default=dict)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(),
                         onupdate=func.now(), nullable=False)


# ── Department ────────────────────────────────────────────────────────────────

class OrgDepartment(Base):
    __tablename__ = "org_departments"
    __table_args__ = (
        Index("idx_org_depts_tenant_org",     "tenant_id", "org_id"),
        Index("idx_org_depts_tenant_status",  "tenant_id", "status"),
        Index("idx_org_depts_parent",         "parent_dept_id"),
        {"schema": None},
    )

    id               = Column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    tenant_id        = Column(PG_UUID(as_uuid=True), nullable=False)
    org_id           = Column(PG_UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)  # noqa: E501
    parent_dept_id   = Column(PG_UUID(as_uuid=True), ForeignKey("org_departments.id"), nullable=True)  # noqa: E501
    name             = Column(String(200), nullable=False)
    purpose          = Column(Text, default="")
    capability_domains = Column(ARRAY(String), default=list)
    manager_agent_id = Column(String(200), nullable=True)
    status           = Column(String(50), nullable=False, default="active", index=True)
    extra_data       = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


# ── Team ──────────────────────────────────────────────────────────────────────

class OrgTeam(Base):
    __tablename__ = "org_teams"
    __table_args__ = (
        Index("idx_org_teams_tenant_org",    "tenant_id", "org_id"),
        Index("idx_org_teams_tenant_status", "tenant_id", "status"),
        {"schema": None},
    )

    id               = Column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    tenant_id        = Column(PG_UUID(as_uuid=True), nullable=False)
    org_id           = Column(PG_UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)  # noqa: E501
    dept_id          = Column(PG_UUID(as_uuid=True), ForeignKey("org_departments.id"), nullable=True, index=True)  # noqa: E501
    name             = Column(String(200), nullable=False)
    purpose          = Column(Text, default="")
    team_type        = Column(String(50), default="persistent")
    manager_agent_id = Column(String(200), nullable=True)
    member_agent_ids = Column(ARRAY(String), default=list)
    capability_ids   = Column(ARRAY(String), default=list)
    tool_ids         = Column(ARRAY(String), default=list)
    model_config     = Column(JSONB, default=dict)
    status           = Column(String(50), nullable=False, default="active", index=True)
    extra_data       = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


# ── Role ──────────────────────────────────────────────────────────────────────

class OrgRole(Base):
    __tablename__ = "org_roles"
    __table_args__ = (
        Index("idx_org_roles_tenant_org", "tenant_id", "org_id"),
        {"schema": None},
    )

    id           = Column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    tenant_id    = Column(PG_UUID(as_uuid=True), nullable=False)
    org_id       = Column(PG_UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True, index=True)  # noqa: E501
    dept_id      = Column(PG_UUID(as_uuid=True), ForeignKey("org_departments.id"), nullable=True, index=True)  # noqa: E501
    name         = Column(String(200), nullable=False)
    description  = Column(Text, default="")
    domain       = Column(String(100), default="")
    seniority    = Column(String(50), default="mid")
    capabilities = Column(ARRAY(String), default=list)
    tools        = Column(ARRAY(String), default=list)
    llm_profile  = Column(JSONB, default=dict)
    status       = Column(String(50), nullable=False, default="active")
    extra_data   = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


# ── Capability ────────────────────────────────────────────────────────────────

class OrgCapability(Base):
    __tablename__ = "org_capabilities"
    __table_args__ = (
        Index("idx_org_caps_tenant", "tenant_id"),
        {"schema": None},
    )

    id                    = Column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    tenant_id             = Column(PG_UUID(as_uuid=True), nullable=False)
    org_id                = Column(PG_UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True, index=True)  # noqa: E501
    name                  = Column(String(200), nullable=False)
    description           = Column(Text, default="")
    domain                = Column(String(100), default="", index=True)
    skills                = Column(ARRAY(String), default=list)
    required_tools        = Column(ARRAY(String), default=list)
    required_models       = Column(ARRAY(String), default=list)
    required_knowledge    = Column(ARRAY(String), default=list)
    needs_human_approval  = Column(Boolean, default=False)
    risk_level            = Column(String(20), default="low")
    extra_data      = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


# ── Mission ───────────────────────────────────────────────────────────────────

class OrgMission(Base):
    __tablename__ = "org_missions"
    __table_args__ = (
        Index("idx_org_missions_tenant_org",      "tenant_id", "org_id"),
        Index("idx_org_missions_tenant_status",   "tenant_id", "org_id", "status"),
        Index("idx_org_missions_tenant_priority", "tenant_id", "org_id", "priority"),
        Index("idx_org_missions_tenant_created",  "tenant_id", "org_id", "created_at"),
        {"schema": None},
    )

    id               = Column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    tenant_id        = Column(PG_UUID(as_uuid=True), nullable=False)
    org_id           = Column(PG_UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)  # noqa: E501
    dept_id          = Column(PG_UUID(as_uuid=True), ForeignKey("org_departments.id"), nullable=True, index=True)  # noqa: E501
    assigned_team_id = Column(PG_UUID(as_uuid=True), ForeignKey("org_teams.id"), nullable=True, index=True)  # noqa: E501
    title            = Column(String(500), nullable=False)
    objective        = Column(Text, default="")
    why              = Column(Text, default="")
    expected_outcome = Column(Text, default="")
    status           = Column(String(50), nullable=False, default="draft", index=True)
    priority         = Column(String(20), nullable=False, default="medium", index=True)
    source           = Column(String(50), default="manual")
    autonomy_level   = Column(Integer, nullable=True)
    success_criteria = Column(JSONB, default=list)
    budget_usd       = Column(Float, nullable=True)
    deadline         = Column(DateTime(timezone=True), nullable=True)
    tags             = Column(ARRAY(String), default=list)
    created_by       = Column(String(200), nullable=True)
    trigger_event    = Column(JSONB, nullable=True)
    outputs          = Column(JSONB, default=list)
    evidence         = Column(JSONB, default=list)
    started_at       = Column(DateTime(timezone=True), nullable=True)
    completed_at     = Column(DateTime(timezone=True), nullable=True)
    extra_data       = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


# ── Workstream ────────────────────────────────────────────────────────────────

class OrgWorkstream(Base):
    __tablename__ = "org_workstreams"
    __table_args__ = (
        Index("idx_org_ws_tenant_mission", "tenant_id", "mission_id"),
        {"schema": None},
    )

    id               = Column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    tenant_id        = Column(PG_UUID(as_uuid=True), nullable=False)
    org_id           = Column(PG_UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)  # noqa: E501
    mission_id       = Column(PG_UUID(as_uuid=True), ForeignKey("org_missions.id"), nullable=False, index=True)  # noqa: E501
    assigned_team_id = Column(PG_UUID(as_uuid=True), ForeignKey("org_teams.id"), nullable=True)
    title            = Column(String(500), nullable=False)
    description      = Column(Text, default="")
    status           = Column(String(50), default="active", index=True)
    order_index      = Column(Integer, default=0)
    extra_data       = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


# ── Task ──────────────────────────────────────────────────────────────────────

class OrgTask(Base):
    __tablename__ = "org_tasks"
    __table_args__ = (
        Index("idx_org_tasks_tenant_org",     "tenant_id", "org_id"),
        Index("idx_org_tasks_tenant_mission", "tenant_id", "mission_id"),
        Index("idx_org_tasks_tenant_status",  "tenant_id", "org_id", "status"),
        Index("idx_org_tasks_depth",          "tenant_id", "org_id", "depth"),   # anti-runaway
        Index("idx_org_tasks_parent",         "parent_task_id"),
        {"schema": None},
    )

    id                   = Column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    tenant_id            = Column(PG_UUID(as_uuid=True), nullable=False)
    org_id               = Column(PG_UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)  # noqa: E501
    mission_id           = Column(PG_UUID(as_uuid=True), ForeignKey("org_missions.id"), nullable=True, index=True)  # noqa: E501
    workstream_id        = Column(PG_UUID(as_uuid=True), ForeignKey("org_workstreams.id"), nullable=True, index=True)  # noqa: E501
    parent_task_id       = Column(PG_UUID(as_uuid=True), ForeignKey("org_tasks.id"), nullable=True)
    assigned_team_id     = Column(PG_UUID(as_uuid=True), ForeignKey("org_teams.id"), nullable=True, index=True)  # noqa: E501
    title                = Column(String(500), nullable=False)
    objective            = Column(Text, default="")
    why                  = Column(Text, default="")
    status               = Column(String(50), nullable=False, default="draft", index=True)
    priority             = Column(String(20), default="medium", index=True)
    depth                = Column(Integer, nullable=False, default=0)       # anti-runaway
    assigned_agent_ids   = Column(ARRAY(String), default=list)
    owner_agent_id       = Column(String(200), nullable=True)
    required_capabilities = Column(ARRAY(String), default=list)
    required_tools       = Column(ARRAY(String), default=list)
    required_models      = Column(ARRAY(String), default=list)
    success_criteria     = Column(JSONB, default=list)
    budget_usd           = Column(Float, nullable=True)
    cost_estimate_usd    = Column(Float, nullable=True)
    actual_cost_usd      = Column(Float, nullable=True)
    risk_level           = Column(String(20), default="low")
    risk_notes           = Column(Text, default="")
    deadline             = Column(DateTime(timezone=True), nullable=True)
    dependencies         = Column(ARRAY(String), default=list)
    linked_goal_id       = Column(String(200), nullable=True)
    expires_at           = Column(DateTime(timezone=True), nullable=True)
    outputs              = Column(JSONB, default=list)
    evidence             = Column(JSONB, default=list)
    audit_trail          = Column(JSONB, default=list)
    started_at           = Column(DateTime(timezone=True), nullable=True)
    completed_at         = Column(DateTime(timezone=True), nullable=True)
    extra_data      = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


# ── Decision ──────────────────────────────────────────────────────────────────

class OrgDecision(Base):
    __tablename__ = "org_decisions"
    __table_args__ = (
        Index("idx_org_decisions_tenant_org",    "tenant_id", "org_id"),
        Index("idx_org_decisions_entity",        "tenant_id", "entity_type", "entity_id"),
        Index("idx_org_decisions_approval",      "tenant_id", "approval_status"),
        {"schema": None},
    )

    id               = Column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    tenant_id        = Column(PG_UUID(as_uuid=True), nullable=False)
    org_id           = Column(PG_UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)  # noqa: E501
    entity_type      = Column(String(100), nullable=False)
    entity_id        = Column(String(200), nullable=False)
    decision_type    = Column(String(100), nullable=False)
    description      = Column(Text, default="")
    why              = Column(Text, default="")
    trigger          = Column(JSONB, nullable=True)
    evidence         = Column(JSONB, default=list)
    policy_refs      = Column(ARRAY(String), default=list)
    expected_outcome = Column(Text, default="")
    risk_level       = Column(String(20), default="low")
    cost_estimate_usd = Column(Float, nullable=True)
    autonomy_level   = Column(Integer, nullable=True)
    approval_status  = Column(String(50), default="auto_approved", index=True)
    actor_agent_id   = Column(String(200), nullable=True)
    extra_data       = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


# ── Event ──────────────────────────────────────────────────────────────────────

class OrgEvent(Base):
    __tablename__ = "org_events"
    __table_args__ = (
        Index("idx_org_events_tenant_org_time", "tenant_id", "org_id", "created_at"),
        Index("idx_org_events_event_type",      "tenant_id", "org_id", "event_type"),
        Index("idx_org_events_severity",        "tenant_id", "org_id", "severity"),
        {"schema": None},
    )

    id          = Column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    tenant_id   = Column(PG_UUID(as_uuid=True), nullable=False)
    org_id      = Column(PG_UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)  # noqa: E501
    event_type  = Column(String(100), nullable=False, index=True)
    title       = Column(String(500), default="")
    description = Column(Text, default="")
    entity_type = Column(String(100), nullable=True)
    entity_id   = Column(String(200), nullable=True)
    severity    = Column(String(20), default="info", index=True)
    payload     = Column(JSONB, default=dict)
    source      = Column(String(100), default="system")
    actor_id    = Column(String(200), nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ── Blueprint (org templates) ─────────────────────────────────────────────────

class OrgBlueprint(Base):
    __tablename__ = "org_blueprints"
    __table_args__ = (
        Index("idx_org_blueprints_domain", "domain"),
        {"schema": None},
    )

    id          = Column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    tenant_id   = Column(PG_UUID(as_uuid=True), nullable=True)   # null = global blueprint
    name        = Column(String(200), nullable=False)
    slug        = Column(String(100), nullable=False, unique=True)
    description = Column(Text, default="")
    domain      = Column(String(100), default="")
    departments = Column(JSONB, default=list)
    roles       = Column(JSONB, default=list)
    capabilities = Column(JSONB, default=list)
    extra_data  = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


# ── Export ────────────────────────────────────────────────────────────────────

__all__ = [
    "OrgBlueprint",
    "OrgCapability",
    "OrgDecision",
    "OrgDepartment",
    "OrgEvent",
    "OrgMission",
    "OrgRole",
    "OrgTask",
    "OrgTeam",
    "OrgWorkstream",
    "Organization",
]
