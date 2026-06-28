"""policy_versions, approval_sla_configs, hitl_approval_requests, policy_evaluations

Revision ID: 0056
Revises: 0048
Create Date: 2026-06-28
"""
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Table: policy_versions                                               #
    # Immutable snapshot of every policy state change                     #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS policy_versions (
            id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id       TEXT NOT NULL,
            policy_id       TEXT NOT NULL,
            version_number  INTEGER NOT NULL,
            name            TEXT NOT NULL,
            description     TEXT,
            rules           JSONB NOT NULL DEFAULT '[]'::jsonb,
            metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_active       BOOLEAN NOT NULL DEFAULT FALSE,
            parent_policy_id TEXT,
            change_summary  TEXT,
            changed_by      TEXT,
            changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at      TIMESTAMPTZ,
            CONSTRAINT uq_policy_version UNIQUE (policy_id, version_number)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_policy_versions_tenant "
        "ON policy_versions(tenant_id, policy_id, version_number DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_policy_versions_active "
        "ON policy_versions(policy_id) WHERE is_active = TRUE"
    )
    op.execute("ALTER TABLE policy_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE policy_versions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY policy_versions_isolation ON policy_versions "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )

    # ------------------------------------------------------------------ #
    # Table: approval_sla_configs                                         #
    # Per-tenant SLA definitions for HITL approvals                      #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS approval_sla_configs (
            id                      TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id               TEXT NOT NULL,
            name                    TEXT NOT NULL,
            description             TEXT,
            risk_level              TEXT NOT NULL,
            response_sla_minutes    INTEGER NOT NULL DEFAULT 60,
            escalation_sla_minutes  INTEGER NOT NULL DEFAULT 120,
            escalation_roles        JSONB NOT NULL DEFAULT '[]'::jsonb,
            escalation_channels     JSONB NOT NULL DEFAULT '[]'::jsonb,
            auto_approve_on_timeout BOOLEAN NOT NULL DEFAULT FALSE,
            auto_deny_on_timeout    BOOLEAN NOT NULL DEFAULT FALSE,
            timeout_minutes         INTEGER NOT NULL DEFAULT 480,
            business_hours_only     BOOLEAN NOT NULL DEFAULT FALSE,
            timezone                TEXT NOT NULL DEFAULT 'UTC',
            is_active               BOOLEAN NOT NULL DEFAULT TRUE,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_sla_tenant_name UNIQUE (tenant_id, name)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sla_configs_tenant "
        "ON approval_sla_configs(tenant_id) WHERE is_active = TRUE"
    )
    op.execute("ALTER TABLE approval_sla_configs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approval_sla_configs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY sla_configs_isolation ON approval_sla_configs "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )

    # ------------------------------------------------------------------ #
    # Table: hitl_approval_requests                                       #
    # Durable HITL requests (replaces asyncio.Event in-process storage)  #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS hitl_approval_requests (
            id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id       TEXT NOT NULL,
            goal_id         TEXT,
            agent_id        TEXT,
            step_id         TEXT NOT NULL,
            request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            risk_level      TEXT NOT NULL DEFAULT 'high',
            status          TEXT NOT NULL DEFAULT 'pending',
            sla_config_id   TEXT,
            sla_deadline    TIMESTAMPTZ,
            escalated_at    TIMESTAMPTZ,
            resolved_by     TEXT,
            resolved_at     TIMESTAMPTZ,
            resolution_note TEXT,
            resolution_data JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_hitl_requests_tenant_status "
        "ON hitl_approval_requests(tenant_id, status, created_at DESC) "
        "WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_hitl_requests_sla "
        "ON hitl_approval_requests(sla_deadline) WHERE status = 'pending'"
    )
    op.execute("ALTER TABLE hitl_approval_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hitl_approval_requests FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY hitl_requests_isolation ON hitl_approval_requests "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )

    # ------------------------------------------------------------------ #
    # Table: policy_evaluations (partitioned by month)                   #
    # Audit trail for every policy evaluation                            #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS policy_evaluations (
            id              TEXT NOT NULL DEFAULT gen_random_uuid()::text,
            tenant_id       TEXT NOT NULL,
            policy_id       TEXT,
            policy_version  INTEGER,
            goal_id         TEXT,
            tool_name       TEXT,
            tool_args_hash  TEXT,
            matched         BOOLEAN NOT NULL,
            match_method    TEXT NOT NULL,
            llm_judge_score NUMERIC(4,3),
            llm_judge_reason TEXT,
            action_taken    TEXT NOT NULL,
            evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, evaluated_at)
        ) PARTITION BY RANGE (evaluated_at)
    """)
    # Create monthly partitions for 2026 and 2027
    for yr in range(2026, 2028):
        for mo in range(1, 13):
            start = f"{yr}-{mo:02d}-01"
            next_mo = mo + 1 if mo < 12 else 1
            next_yr = yr if mo < 12 else yr + 1
            end = f"{next_yr}-{next_mo:02d}-01"
            tname = f"policy_evaluations_{yr}_{mo:02d}"
            op.execute(
                f"CREATE TABLE IF NOT EXISTS {tname} "
                f"PARTITION OF policy_evaluations "
                f"FOR VALUES FROM ('{start}') TO ('{end}')"
            )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_policy_evals_tenant "
        "ON policy_evaluations(tenant_id, evaluated_at DESC)"
    )
    op.execute("ALTER TABLE policy_evaluations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE policy_evaluations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY policy_evals_isolation ON policy_evaluations "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS policy_evaluations CASCADE")
    op.execute("DROP TABLE IF EXISTS hitl_approval_requests CASCADE")
    op.execute("DROP TABLE IF EXISTS approval_sla_configs CASCADE")
    op.execute("DROP TABLE IF EXISTS policy_versions CASCADE")
