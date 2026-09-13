"""org_brain_decisions — audit trail for the autonomous org brain's tick decisions.

Every tick (proactive scan or reactive response) the org brain makes is recorded
here: what it considered (kind/rationale/target_goal), what the guardrails
decided (guardrail_verdict/reason/est_cost_usd), and what actually happened
(action). Written by BrainDecisionStore.record, read back by
BrainDecisionStore.list. Tenant-isolated via RLS like the other org_* tables
(see 0126).

Revision ID: 0128
Revises: 0127
"""

from __future__ import annotations

from alembic import op

revision = "0128"
down_revision = "0127"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS org_brain_decisions (
            id                UUID PRIMARY KEY,
            org_id            UUID NOT NULL REFERENCES organizations(id),
            tenant_id         UUID NOT NULL,
            tick_id           VARCHAR(64) NOT NULL,
            kind              VARCHAR(32) NOT NULL,
            rationale         TEXT DEFAULT '',
            target_goal       VARCHAR(200) DEFAULT '',
            action            VARCHAR(32) NOT NULL,
            guardrail_verdict VARCHAR(32) NOT NULL,
            reason            TEXT DEFAULT '',
            est_cost_usd      DOUBLE PRECISION DEFAULT 0,
            mission_id        UUID,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_obd_org_created "
        "ON org_brain_decisions (org_id, created_at)"
    )
    # Row-level tenant isolation, matching the other org_* tables (see 0126).
    op.execute("ALTER TABLE org_brain_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_brain_decisions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'org_brain_decisions'
                  AND policyname = 'org_brain_decisions_isolation'
            ) THEN
                CREATE POLICY org_brain_decisions_isolation ON org_brain_decisions
                    USING (tenant_id::text = current_setting('app.tenant_id', TRUE));
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS org_brain_decisions CASCADE")
