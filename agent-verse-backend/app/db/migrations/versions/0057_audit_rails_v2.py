"""Partitioned audit_events, legal_holds, siem_configs, audit WAL queue

Revision ID: 0057
Revises: 0056
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMPTZ

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Table: audit_events (partitioned by created_at month)              #
    # Replaces the in-memory audit dict — durable + tamper-evident       #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id              TEXT NOT NULL DEFAULT gen_random_uuid()::text,
            tenant_id       TEXT NOT NULL,
            user_id         TEXT,
            api_key_id      TEXT,
            actor_type      TEXT NOT NULL DEFAULT 'system',
            actor_label     TEXT,
            event_type      TEXT NOT NULL,
            resource_type   TEXT,
            resource_id     TEXT,
            resource_label  TEXT,
            action          TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'success',
            error_code      TEXT,
            error_message   TEXT,
            ip_address      TEXT,
            user_agent      TEXT,
            request_id      TEXT,
            goal_id         TEXT,
            agent_id        TEXT,
            metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
            tool_name       TEXT,
            tool_args_hash  TEXT,
            tool_args_safe  JSONB,
            prev_hash       TEXT NOT NULL DEFAULT '',
            event_hash      TEXT NOT NULL DEFAULT '',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    """)

    # Create monthly partitions for 2026 and 2027
    for yr in range(2026, 2028):
        for mo in range(1, 13):
            start = f"{yr}-{mo:02d}-01"
            next_mo = mo + 1 if mo < 12 else 1
            next_yr = yr if mo < 12 else yr + 1
            end = f"{next_yr}-{next_mo:02d}-01"
            tname = f"audit_events_{yr}_{mo:02d}"
            op.execute(
                f"CREATE TABLE IF NOT EXISTS {tname} "
                f"PARTITION OF audit_events "
                f"FOR VALUES FROM ('{start}') TO ('{end}')"
            )

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_tenant_time "
        "ON audit_events(tenant_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_resource "
        "ON audit_events(tenant_id, resource_type, resource_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_actor "
        "ON audit_events(tenant_id, user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_event_type "
        "ON audit_events(tenant_id, event_type, created_at DESC)"
    )
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY audit_events_isolation ON audit_events "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )

    # Legal hold enforcement trigger (prevents deletion of held records)
    op.execute("""
        CREATE OR REPLACE FUNCTION check_legal_hold_before_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM legal_holds lh
                WHERE lh.tenant_id = OLD.tenant_id
                  AND lh.status = 'active'
                  AND (
                      (OLD.resource_id IS NOT NULL
                       AND OLD.resource_id = ANY(
                           SELECT jsonb_array_elements_text(lh.resource_ids)
                       ))
                      OR
                      (OLD.user_id IS NOT NULL
                       AND OLD.user_id = ANY(
                           SELECT jsonb_array_elements_text(lh.user_ids)
                       ))
                  )
                  AND (lh.date_range_start IS NULL OR OLD.created_at >= lh.date_range_start)
                  AND (lh.date_range_end   IS NULL OR OLD.created_at <= lh.date_range_end)
            ) THEN
                RAISE EXCEPTION 'Cannot delete audit event: record is under legal hold';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
    """)

    # ------------------------------------------------------------------ #
    # Table: audit_wal_queue                                             #
    # Dead-letter queue for audit events that failed DB insertion        #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_wal_queue (
            id          BIGSERIAL PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            payload     JSONB NOT NULL,
            attempts    INTEGER NOT NULL DEFAULT 0,
            last_error  TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            inserted_at TIMESTAMPTZ
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_wal_pending "
        "ON audit_wal_queue(created_at) WHERE inserted_at IS NULL"
    )

    # ------------------------------------------------------------------ #
    # Table: legal_holds                                                 #
    # Prevents deletion of audit events for specific resources           #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS legal_holds (
            id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id        TEXT NOT NULL,
            name             TEXT NOT NULL,
            description      TEXT,
            resource_type    TEXT NOT NULL,
            resource_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
            user_ids         JSONB NOT NULL DEFAULT '[]'::jsonb,
            date_range_start TIMESTAMPTZ,
            date_range_end   TIMESTAMPTZ,
            status           TEXT NOT NULL DEFAULT 'active',
            legal_matter_id  TEXT,
            created_by       TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            released_at      TIMESTAMPTZ,
            released_by      TEXT,
            release_reason   TEXT,
            expires_at       TIMESTAMPTZ
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_legal_holds_tenant "
        "ON legal_holds(tenant_id) WHERE status = 'active'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_legal_holds_resource "
        "ON legal_holds(resource_type, tenant_id) WHERE status = 'active'"
    )
    op.execute("ALTER TABLE legal_holds ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE legal_holds FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY legal_holds_isolation ON legal_holds "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )

    # Attach the trigger to audit_events now that legal_holds exists
    op.execute("""
        CREATE TRIGGER trg_audit_legal_hold_check
            BEFORE DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION check_legal_hold_before_delete()
    """)

    # ------------------------------------------------------------------ #
    # Table: siem_configs                                                #
    # Per-tenant SIEM integration configurations                         #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS siem_configs (
            id                       TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id                TEXT NOT NULL,
            name                     TEXT NOT NULL,
            siem_type                TEXT NOT NULL,
            is_active                BOOLEAN NOT NULL DEFAULT TRUE,
            config_encrypted         TEXT NOT NULL DEFAULT '',
            event_filter             JSONB NOT NULL DEFAULT '{}'::jsonb,
            min_severity             TEXT DEFAULT 'low',
            batch_size               INTEGER NOT NULL DEFAULT 100,
            flush_interval_seconds   INTEGER NOT NULL DEFAULT 30,
            last_flush_at            TIMESTAMPTZ,
            last_error               TEXT,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_siem_tenant_name UNIQUE (tenant_id, name)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_siem_configs_tenant "
        "ON siem_configs(tenant_id) WHERE is_active = TRUE"
    )
    op.execute("ALTER TABLE siem_configs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE siem_configs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY siem_configs_isolation ON siem_configs "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS siem_configs CASCADE")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_legal_hold_check ON audit_events")
    op.execute("DROP TABLE IF EXISTS legal_holds CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_wal_queue CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_events CASCADE")
    op.execute("DROP FUNCTION IF EXISTS check_legal_hold_before_delete")
