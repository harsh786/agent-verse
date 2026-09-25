"""Persist the trust-governance approval and AI-Ops eval state.

`app/api/trust_governance.py` and `app/api/ai_ops.py` are mounted, live routers
whose entire state lived in module-level dicts:

    _approvals, _approval_delegations          (trust_governance)
    _datasets, _eval_results, _drift_alerts,
    _judges, _baselines                        (ai_ops)

trust_governance even says "In-memory for demo; production uses DB" — but no DB
path existed, so the demo store *was* production. Approvals vanished on every
restart/redeploy and were invisible to every other replica, meaning an approval
granted on one pod did not exist for the pod that served the next request.

`trust_approval_votes` carries UNIQUE (request_id, approver_id) so the
separation-of-duties rule — `required_approvers` counts DISTINCT approvers — is
a database invariant rather than an application-level check, and therefore holds
against concurrent approvals landing on different replicas.

All tables use ENABLE + FORCE row level security with the standard
`current_setting('app.tenant_id', TRUE)` policy.

Revision ID: f4c2a8e91b57
Revises: e83b17c40d92
"""

from __future__ import annotations

from alembic import op

revision = "f4c2a8e91b57"
down_revision = "e83b17c40d92"
branch_labels = None
depends_on = None

_TABLES = (
    "trust_approval_requests",
    "trust_approval_votes",
    "ai_ops_datasets",
    "ai_ops_eval_results",
    "ai_ops_judges",
    "ai_ops_baselines",
    "ai_ops_drift_alerts",
)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS trust_approval_requests (
            id                 TEXT PRIMARY KEY,
            tenant_id          TEXT NOT NULL,
            goal_id            TEXT,
            step_description   TEXT NOT NULL DEFAULT '',
            tool_name          TEXT NOT NULL DEFAULT '',
            risk_level         TEXT NOT NULL DEFAULT 'high',
            required_approvers INTEGER NOT NULL DEFAULT 1
                               CHECK (required_approvers >= 1),
            status             TEXT NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending','approved','rejected')),
            rejection_reason   TEXT,
            rejected_by        TEXT,
            resolved_at        TIMESTAMPTZ,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_trust_approvals_tenant_status "
        "ON trust_approval_requests(tenant_id, status, created_at DESC)"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS trust_approval_votes (
            id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            request_id  TEXT NOT NULL
                        REFERENCES trust_approval_requests(id) ON DELETE CASCADE,
            tenant_id   TEXT NOT NULL,
            approver_id TEXT NOT NULL,
            action      TEXT NOT NULL DEFAULT 'approved',
            note        TEXT NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_trust_vote_per_approver UNIQUE (request_id, approver_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_trust_votes_request "
        "ON trust_approval_votes(request_id)"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_ops_datasets (
            id           TEXT PRIMARY KEY,
            tenant_id    TEXT NOT NULL,
            name         TEXT NOT NULL,
            description  TEXT NOT NULL DEFAULT '',
            golden_tasks JSONB NOT NULL DEFAULT '[]'::jsonb,
            version      INTEGER NOT NULL DEFAULT 1,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_ops_datasets_tenant "
        "ON ai_ops_datasets(tenant_id, created_at DESC)"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_ops_eval_results (
            id         TEXT PRIMARY KEY,
            tenant_id  TEXT NOT NULL,
            dataset_id TEXT,
            payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_ops_eval_results_tenant "
        "ON ai_ops_eval_results(tenant_id, created_at DESC)"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_ops_judges (
            id         TEXT PRIMARY KEY,
            tenant_id  TEXT NOT NULL,
            payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_ops_judges_tenant "
        "ON ai_ops_judges(tenant_id, created_at DESC)"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_ops_baselines (
            tenant_id   TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            value       DOUBLE PRECISION NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, metric_name)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_ops_drift_alerts (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            severity    TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_ops_alerts_tenant "
        "ON ai_ops_drift_alerts(tenant_id, severity, created_at DESC)"
    )

    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
