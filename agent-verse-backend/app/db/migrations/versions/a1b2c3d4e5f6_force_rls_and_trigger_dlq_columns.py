"""FORCE ROW LEVEL SECURITY on the 21 ENABLE-only tables + trigger_dlq columns.

Two independent defects in one revision because both are schema-level and both
are exercised by the same trigger/DLQ regression tests.

**1. ENABLE without FORCE.** 80 of this schema's 101 RLS-protected tables are
declared ``ENABLE ROW LEVEL SECURITY`` *and* ``FORCE ROW LEVEL SECURITY``. 21 are
not forced::

    api_key_scopes, artifacts, channel_tenant_mappings, custom_roles,
    governance_policies, ingestion_dlq, ingestion_jobs, ip_allowlist_entries,
    role_assignments, state_machine_instances, state_machines,
    trigger_audit_events, trigger_dlq, trigger_events,
    workflow_definition_versions, workflow_hitl_requests, workflow_permissions,
    workflow_runs, workflow_step_results, workflow_test_scenarios,
    workflow_webhook_events

``ENABLE`` alone exempts the *table owner* from its own policies. The owner is
the role that runs these migrations, and it is also the role the default
``DATABASE_URL`` (``agentverse:agentverse@``) and every docker-compose/dev stack
connects as — so for those 21 tables RLS was silently a no-op and tenant
isolation rested entirely on each query remembering its own ``tenant_id``
predicate. It did not always remember: ``GET /triggers/{id}/events`` filtered on
``trigger_id`` alone (fixed in ``app/api/triggers.py`` alongside this revision),
which leaked another tenant's raw webhook payloads. The 21 tables are the
outlier, not the design; this brings them to parity.

**2. ``trigger_dlq`` is missing the columns its own read path selects.**
``GET /triggers/dlq`` issues ``SELECT ... next_retry_at, created_at FROM
trigger_dlq ORDER BY created_at DESC`` but migration 0106 created neither column.
Every call raised ``UndefinedColumnError``, swallowed by a broad
``except Exception: return []`` — the DLQ endpoint has never returned a row since
it was written. ``next_retry_at`` is also what a real retry has to schedule into,
so add both (``created_at`` backfilled from ``failed_at``) plus the index the
listing's ``WHERE tenant_id = ... ORDER BY created_at DESC`` needs.

Revision ID: a1b2c3d4e5f6
Revises: f4c2a8e91b57
"""

from __future__ import annotations

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f4c2a8e91b57"
branch_labels = None
depends_on = None


# Tables that declare ENABLE ROW LEVEL SECURITY but never FORCE it.
_UNFORCED_TABLES = (
    "api_key_scopes",
    "artifacts",
    "channel_tenant_mappings",
    "custom_roles",
    "governance_policies",
    "ingestion_dlq",
    "ingestion_jobs",
    "ip_allowlist_entries",
    "role_assignments",
    "state_machine_instances",
    "state_machines",
    "trigger_audit_events",
    "trigger_dlq",
    "trigger_events",
    "workflow_definition_versions",
    "workflow_hitl_requests",
    "workflow_permissions",
    "workflow_runs",
    "workflow_step_results",
    "workflow_test_scenarios",
    "workflow_webhook_events",
)


def upgrade() -> None:
    # ── 1. trigger_dlq: the columns the read/retry paths already assume ───────
    op.execute("ALTER TABLE trigger_dlq ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE trigger_dlq ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ "
        "NOT NULL DEFAULT now()"
    )
    # Rows that predate the column get the time they actually failed, not "now".
    op.execute("UPDATE trigger_dlq SET created_at = failed_at WHERE failed_at IS NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_trigger_dlq_tenant_created "
        "ON trigger_dlq (tenant_id, created_at DESC)"
    )
    # A retry sweeper scans for entries whose next attempt is due.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_trigger_dlq_due "
        "ON trigger_dlq (next_retry_at) "
        "WHERE next_retry_at IS NOT NULL AND resolved_at IS NULL"
    )

    # ── 2. FORCE RLS parity ──────────────────────────────────────────────────
    for table in _UNFORCED_TABLES:
        # IF EXISTS keeps this revision applicable to schemas where an optional
        # table was never created.
        op.execute(f"ALTER TABLE IF EXISTS {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in _UNFORCED_TABLES:
        op.execute(f"ALTER TABLE IF EXISTS {table} NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS idx_trigger_dlq_due")
    op.execute("DROP INDEX IF EXISTS idx_trigger_dlq_tenant_created")
    op.execute("ALTER TABLE trigger_dlq DROP COLUMN IF EXISTS created_at")
    op.execute("ALTER TABLE trigger_dlq DROP COLUMN IF EXISTS next_retry_at")
