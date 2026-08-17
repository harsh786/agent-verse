"""0111 — AI Organization OS tables.

Creates the complete AI Organization Operating System schema:
  - organizations
  - org_departments
  - org_teams
  - org_roles
  - org_capabilities
  - org_missions
  - org_workstreams
  - org_tasks
  - org_decisions
  - org_events
  - org_blueprints

All tables:
  - Are tenant-isolated via RLS policies
  - Have UUID v7 primary keys (time-sortable)
  - Include tenant_id + created_at + updated_at
  - Use CONCURRENTLY indexes (non-locking)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── organizations ──────────────────────────────────────────────────────────
    op.create_table(
        "organizations",
        sa.Column("id",                  UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",           UUID(as_uuid=True), nullable=False),
        sa.Column("name",                sa.String(200),     nullable=False),
        sa.Column("slug",                sa.String(64),      nullable=False),
        sa.Column("description",         sa.Text,            server_default=""),
        sa.Column("industry",            sa.String(100),     server_default=""),
        sa.Column("jurisdiction",        sa.String(100),     server_default=""),
        sa.Column("mission",             sa.Text,            server_default=""),
        sa.Column("vision",              sa.Text,            server_default=""),
        sa.Column("status",              sa.String(50),      nullable=False, server_default="active"),
        sa.Column("autonomy_level",      sa.Integer,         nullable=False, server_default="1"),
        sa.Column("risk_tolerance",      sa.String(20),      server_default="medium"),
        sa.Column("monthly_budget_usd",  sa.Float,           server_default="0"),
        sa.Column("goals",               JSONB,              server_default="[]"),
        sa.Column("policies",            JSONB,              server_default="{}"),
        sa.Column("settings",            JSONB,              server_default="{}"),
        sa.Column("blueprint_ids",       ARRAY(sa.String),   server_default="{}"),
        sa.Column("created_by",          sa.String(200),     nullable=True),
        sa.Column("metadata",            JSONB,              server_default="{}"),
        sa.Column("created_at",          sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",          sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_orgs_tenant_id      ON organizations(tenant_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_orgs_tenant_status  ON organizations(tenant_id, status)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_orgs_tenant_created ON organizations(tenant_id, created_at DESC)")

    # RLS
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON organizations
        USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)
    """)

    # ── org_departments ────────────────────────────────────────────────────────
    op.create_table(
        "org_departments",
        sa.Column("id",               UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",        UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",           UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_dept_id",   UUID(as_uuid=True), sa.ForeignKey("org_departments.id"), nullable=True),
        sa.Column("name",             sa.String(200),     nullable=False),
        sa.Column("purpose",          sa.Text,            server_default=""),
        sa.Column("capability_domains", ARRAY(sa.String), server_default="{}"),
        sa.Column("manager_agent_id", sa.String(200),     nullable=True),
        sa.Column("status",           sa.String(50),      nullable=False, server_default="active"),
        sa.Column("metadata",         JSONB,              server_default="{}"),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_depts_tenant_org    ON org_departments(tenant_id, org_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_depts_tenant_status ON org_departments(tenant_id, status)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_depts_parent        ON org_departments(parent_dept_id)")
    op.execute("ALTER TABLE org_departments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_departments FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON org_departments
        USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)""")

    # ── org_teams ──────────────────────────────────────────────────────────────
    op.create_table(
        "org_teams",
        sa.Column("id",               UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",        UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",           UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dept_id",          UUID(as_uuid=True), sa.ForeignKey("org_departments.id"), nullable=True),
        sa.Column("name",             sa.String(200),     nullable=False),
        sa.Column("purpose",          sa.Text,            server_default=""),
        sa.Column("team_type",        sa.String(50),      server_default="persistent"),
        sa.Column("manager_agent_id", sa.String(200),     nullable=True),
        sa.Column("member_agent_ids", ARRAY(sa.String),   server_default="{}"),
        sa.Column("capability_ids",   ARRAY(sa.String),   server_default="{}"),
        sa.Column("tool_ids",         ARRAY(sa.String),   server_default="{}"),
        sa.Column("model_config",     JSONB,              server_default="{}"),
        sa.Column("status",           sa.String(50),      nullable=False, server_default="active"),
        sa.Column("metadata",         JSONB,              server_default="{}"),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_teams_tenant_org    ON org_teams(tenant_id, org_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_teams_tenant_status ON org_teams(tenant_id, status)")
    op.execute("ALTER TABLE org_teams ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_teams FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON org_teams
        USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)""")

    # ── org_roles ──────────────────────────────────────────────────────────────
    op.create_table(
        "org_roles",
        sa.Column("id",           UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",    UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",       UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("dept_id",      UUID(as_uuid=True), sa.ForeignKey("org_departments.id"), nullable=True),
        sa.Column("name",         sa.String(200),     nullable=False),
        sa.Column("description",  sa.Text,            server_default=""),
        sa.Column("domain",       sa.String(100),     server_default=""),
        sa.Column("seniority",    sa.String(50),      server_default="mid"),
        sa.Column("capabilities", ARRAY(sa.String),   server_default="{}"),
        sa.Column("tools",        ARRAY(sa.String),   server_default="{}"),
        sa.Column("llm_profile",  JSONB,              server_default="{}"),
        sa.Column("status",       sa.String(50),      server_default="active"),
        sa.Column("metadata",     JSONB,              server_default="{}"),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",   sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_roles_tenant_org ON org_roles(tenant_id, org_id)")
    op.execute("ALTER TABLE org_roles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_roles FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON org_roles
        USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)""")

    # ── org_capabilities ──────────────────────────────────────────────────────
    op.create_table(
        "org_capabilities",
        sa.Column("id",                   UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",            UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",               UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("name",                 sa.String(200),     nullable=False),
        sa.Column("description",          sa.Text,            server_default=""),
        sa.Column("domain",               sa.String(100),     server_default=""),
        sa.Column("skills",               ARRAY(sa.String),   server_default="{}"),
        sa.Column("required_tools",       ARRAY(sa.String),   server_default="{}"),
        sa.Column("required_models",      ARRAY(sa.String),   server_default="{}"),
        sa.Column("required_knowledge",   ARRAY(sa.String),   server_default="{}"),
        sa.Column("needs_human_approval", sa.Boolean,         server_default="false"),
        sa.Column("risk_level",           sa.String(20),      server_default="low"),
        sa.Column("metadata",             JSONB,              server_default="{}"),
        sa.Column("created_at",           sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",           sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_caps_tenant ON org_capabilities(tenant_id)")
    op.execute("ALTER TABLE org_capabilities ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_capabilities FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON org_capabilities
        USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)""")

    # ── org_missions ──────────────────────────────────────────────────────────
    op.create_table(
        "org_missions",
        sa.Column("id",               UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",        UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",           UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dept_id",          UUID(as_uuid=True), sa.ForeignKey("org_departments.id"), nullable=True),
        sa.Column("assigned_team_id", UUID(as_uuid=True), sa.ForeignKey("org_teams.id"), nullable=True),
        sa.Column("title",            sa.String(500),     nullable=False),
        sa.Column("objective",        sa.Text,            server_default=""),
        sa.Column("why",              sa.Text,            server_default=""),
        sa.Column("expected_outcome", sa.Text,            server_default=""),
        sa.Column("status",           sa.String(50),      nullable=False, server_default="draft"),
        sa.Column("priority",         sa.String(20),      nullable=False, server_default="medium"),
        sa.Column("source",           sa.String(50),      server_default="manual"),
        sa.Column("autonomy_level",   sa.Integer,         nullable=True),
        sa.Column("success_criteria", JSONB,              server_default="[]"),
        sa.Column("budget_usd",       sa.Float,           nullable=True),
        sa.Column("deadline",         sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags",             ARRAY(sa.String),   server_default="{}"),
        sa.Column("created_by",       sa.String(200),     nullable=True),
        sa.Column("trigger_event",    JSONB,              nullable=True),
        sa.Column("outputs",          JSONB,              server_default="[]"),
        sa.Column("evidence",         JSONB,              server_default="[]"),
        sa.Column("started_at",       sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata",         JSONB,              server_default="{}"),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_missions_tenant_org      ON org_missions(tenant_id, org_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_missions_tenant_status   ON org_missions(tenant_id, org_id, status)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_missions_tenant_priority ON org_missions(tenant_id, org_id, priority)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_missions_tenant_created  ON org_missions(tenant_id, org_id, created_at DESC)")
    op.execute("ALTER TABLE org_missions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_missions FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON org_missions
        USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)""")

    # ── org_workstreams ────────────────────────────────────────────────────────
    op.create_table(
        "org_workstreams",
        sa.Column("id",               UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",        UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",           UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mission_id",       UUID(as_uuid=True), sa.ForeignKey("org_missions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_team_id", UUID(as_uuid=True), sa.ForeignKey("org_teams.id"), nullable=True),
        sa.Column("title",            sa.String(500),     nullable=False),
        sa.Column("description",      sa.Text,            server_default=""),
        sa.Column("status",           sa.String(50),      server_default="active"),
        sa.Column("order_index",      sa.Integer,         server_default="0"),
        sa.Column("metadata",         JSONB,              server_default="{}"),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_ws_tenant_mission ON org_workstreams(tenant_id, mission_id)")
    op.execute("ALTER TABLE org_workstreams ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_workstreams FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON org_workstreams
        USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)""")

    # ── org_tasks ─────────────────────────────────────────────────────────────
    op.create_table(
        "org_tasks",
        sa.Column("id",                    UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",             UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",                UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mission_id",            UUID(as_uuid=True), sa.ForeignKey("org_missions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("workstream_id",         UUID(as_uuid=True), sa.ForeignKey("org_workstreams.id"), nullable=True),
        sa.Column("parent_task_id",        UUID(as_uuid=True), sa.ForeignKey("org_tasks.id"), nullable=True),
        sa.Column("assigned_team_id",      UUID(as_uuid=True), sa.ForeignKey("org_teams.id"), nullable=True),
        sa.Column("title",                 sa.String(500),     nullable=False),
        sa.Column("objective",             sa.Text,            server_default=""),
        sa.Column("why",                   sa.Text,            server_default=""),
        sa.Column("status",                sa.String(50),      nullable=False, server_default="draft"),
        sa.Column("priority",              sa.String(20),      server_default="medium"),
        sa.Column("depth",                 sa.Integer,         nullable=False, server_default="0"),
        sa.Column("assigned_agent_ids",    ARRAY(sa.String),   server_default="{}"),
        sa.Column("owner_agent_id",        sa.String(200),     nullable=True),
        sa.Column("required_capabilities", ARRAY(sa.String),   server_default="{}"),
        sa.Column("required_tools",        ARRAY(sa.String),   server_default="{}"),
        sa.Column("required_models",       ARRAY(sa.String),   server_default="{}"),
        sa.Column("success_criteria",      JSONB,              server_default="[]"),
        sa.Column("budget_usd",            sa.Float,           nullable=True),
        sa.Column("cost_estimate_usd",     sa.Float,           nullable=True),
        sa.Column("actual_cost_usd",       sa.Float,           nullable=True),
        sa.Column("risk_level",            sa.String(20),      server_default="low"),
        sa.Column("risk_notes",            sa.Text,            server_default=""),
        sa.Column("deadline",              sa.DateTime(timezone=True), nullable=True),
        sa.Column("dependencies",          ARRAY(sa.String),   server_default="{}"),
        sa.Column("linked_goal_id",        sa.String(200),     nullable=True),
        sa.Column("expires_at",            sa.DateTime(timezone=True), nullable=True),
        sa.Column("outputs",               JSONB,              server_default="[]"),
        sa.Column("evidence",              JSONB,              server_default="[]"),
        sa.Column("audit_trail",           JSONB,              server_default="[]"),
        sa.Column("started_at",            sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at",          sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata",              JSONB,              server_default="{}"),
        sa.Column("created_at",            sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",            sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_tasks_tenant_org     ON org_tasks(tenant_id, org_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_tasks_tenant_mission ON org_tasks(tenant_id, mission_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_tasks_tenant_status  ON org_tasks(tenant_id, org_id, status)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_tasks_depth          ON org_tasks(tenant_id, org_id, depth)")  # anti-runaway
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_tasks_parent         ON org_tasks(parent_task_id)")
    op.execute("ALTER TABLE org_tasks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_tasks FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON org_tasks
        USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)""")

    # ── org_decisions ─────────────────────────────────────────────────────────
    op.create_table(
        "org_decisions",
        sa.Column("id",               UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",        UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",           UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type",      sa.String(100),     nullable=False),
        sa.Column("entity_id",        sa.String(200),     nullable=False),
        sa.Column("decision_type",    sa.String(100),     nullable=False),
        sa.Column("description",      sa.Text,            server_default=""),
        sa.Column("why",              sa.Text,            server_default=""),
        sa.Column("trigger",          JSONB,              nullable=True),
        sa.Column("evidence",         JSONB,              server_default="[]"),
        sa.Column("policy_refs",      ARRAY(sa.String),   server_default="{}"),
        sa.Column("expected_outcome", sa.Text,            server_default=""),
        sa.Column("risk_level",       sa.String(20),      server_default="low"),
        sa.Column("cost_estimate_usd", sa.Float,          nullable=True),
        sa.Column("autonomy_level",   sa.Integer,         nullable=True),
        sa.Column("approval_status",  sa.String(50),      server_default="auto_approved"),
        sa.Column("actor_agent_id",   sa.String(200),     nullable=True),
        sa.Column("metadata",         JSONB,              server_default="{}"),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_decisions_tenant_org    ON org_decisions(tenant_id, org_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_decisions_entity        ON org_decisions(tenant_id, entity_type, entity_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_decisions_approval      ON org_decisions(tenant_id, approval_status)")
    op.execute("ALTER TABLE org_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_decisions FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON org_decisions
        USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)""")

    # ── org_events ────────────────────────────────────────────────────────────
    op.create_table(
        "org_events",
        sa.Column("id",          UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",   UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",      UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type",  sa.String(100),     nullable=False),
        sa.Column("title",       sa.String(500),     server_default=""),
        sa.Column("description", sa.Text,            server_default=""),
        sa.Column("entity_type", sa.String(100),     nullable=True),
        sa.Column("entity_id",   sa.String(200),     nullable=True),
        sa.Column("severity",    sa.String(20),      server_default="info"),
        sa.Column("payload",     JSONB,              server_default="{}"),
        sa.Column("source",      sa.String(100),     server_default="system"),
        sa.Column("actor_id",    sa.String(200),     nullable=True),
        sa.Column("created_at",  sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_events_tenant_org_time ON org_events(tenant_id, org_id, created_at DESC)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_events_event_type      ON org_events(tenant_id, org_id, event_type)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_events_severity        ON org_events(tenant_id, org_id, severity)")
    op.execute("ALTER TABLE org_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_events FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON org_events
        USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)""")

    # ── org_blueprints ────────────────────────────────────────────────────────
    op.create_table(
        "org_blueprints",
        sa.Column("id",           UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",    UUID(as_uuid=True), nullable=True),  # null = global
        sa.Column("name",         sa.String(200),     nullable=False),
        sa.Column("slug",         sa.String(100),     nullable=False),
        sa.Column("description",  sa.Text,            server_default=""),
        sa.Column("domain",       sa.String(100),     server_default=""),
        sa.Column("departments",  JSONB,              server_default="[]"),
        sa.Column("roles",        JSONB,              server_default="[]"),
        sa.Column("capabilities", JSONB,              server_default="[]"),
        sa.Column("metadata",     JSONB,              server_default="{}"),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",   sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_blueprints_domain ON org_blueprints(domain)")
    op.execute("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_org_blueprints_slug ON org_blueprints(slug)")


def downgrade() -> None:
    for table in [
        "org_blueprints", "org_events", "org_decisions", "org_tasks",
        "org_workstreams", "org_missions", "org_capabilities",
        "org_roles", "org_teams", "org_departments", "organizations",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
