"""Durable billing orders + erasure job state.

* ``billing_orders`` — the server-side record of every payment order
  (``/billing/create-order``). ``/billing/verify-payment`` and the Razorpay
  webhook derive the plan ONLY from this row (plan / cycle / amount were fixed
  by the server when the order was created) — never from the client body or
  the provider's free-form ``notes``. Tenant-isolated (FORCE RLS).
* ``deleted_tenants`` — gains job-state columns so a GDPR tenant erasure is a
  durable job processed by the ``process_tenant_erasures`` beat task (status,
  due time, attempts, last error, result) instead of a row nothing ever read.
  Tenants keep their existing request/read/delete policies; status transitions
  are made by the maintenance role (BYPASSRLS) from the beat task.
* ``goal_feedback.processed_at`` — the column ``process_feedback_batch`` has
  always filtered/marked on, but which no migration ever created.
* ``dpdp_erasure_requests.claimed_at`` — lets the beat task claim a request
  (FOR UPDATE SKIP LOCKED → status 'processing') and reclaim one whose worker
  died, so replicas never double-run nor strand a request.

Revision ID: c7e1a9d4b3f2
Revises: a9b8c7d6e5f4
"""

from __future__ import annotations

from alembic import op

revision = "c7e1a9d4b3f2"
down_revision = "a9b8c7d6e5f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS billing_orders (
            order_id     TEXT        PRIMARY KEY,
            tenant_id    TEXT        NOT NULL,
            provider     TEXT        NOT NULL DEFAULT 'razorpay',
            plan         TEXT        NOT NULL,
            cycle        TEXT        NOT NULL,
            amount       BIGINT      NOT NULL,
            currency     TEXT        NOT NULL,
            is_mock      BOOLEAN     NOT NULL DEFAULT FALSE,
            status       TEXT        NOT NULL DEFAULT 'created',
            payment_id   TEXT        NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            paid_at      TIMESTAMPTZ NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_billing_orders_tenant ON billing_orders (tenant_id)"
    )
    op.execute("ALTER TABLE billing_orders ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE billing_orders FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS billing_orders_tenant_isolation ON billing_orders")
    op.execute(
        "CREATE POLICY billing_orders_tenant_isolation ON billing_orders "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )

    for ddl in (
        "ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'",
        "ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ NULL",
        "ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0",
        "ADD COLUMN IF NOT EXISTS last_error TEXT NULL",
        "ADD COLUMN IF NOT EXISTS result JSONB NULL",
        "ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ NULL",
        "ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ NULL",
    ):
        op.execute(f"ALTER TABLE deleted_tenants {ddl}")
    # Pre-existing requests were recorded with no due time: honour the 30-day
    # grace period the API promised from their original request time.
    op.execute(
        "UPDATE deleted_tenants SET scheduled_for = requested_at + INTERVAL '30 days' "
        "WHERE scheduled_for IS NULL"
    )
    op.execute(
        "ALTER TABLE deleted_tenants ALTER COLUMN scheduled_for "
        "SET DEFAULT (NOW() + INTERVAL '30 days')"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_deleted_tenants_due "
        "ON deleted_tenants (scheduled_for) WHERE status <> 'completed'"
    )

    op.execute(
        "ALTER TABLE dpdp_erasure_requests ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_dpdp_erasure_requests_open "
        "ON dpdp_erasure_requests (requested_at) WHERE status IN ('pending', 'processing')"
    )

    # process_feedback_batch marks rows processed; the column never existed, so
    # the batch (scan and per-tenant pass) could never work.
    op.execute("ALTER TABLE goal_feedback ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goal_feedback_unprocessed "
        "ON goal_feedback (tenant_id, created_at) WHERE processed_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_goal_feedback_unprocessed")
    op.execute("ALTER TABLE goal_feedback DROP COLUMN IF EXISTS processed_at")
    op.execute("DROP INDEX IF EXISTS ix_dpdp_erasure_requests_open")
    op.execute("ALTER TABLE dpdp_erasure_requests DROP COLUMN IF EXISTS claimed_at")
    op.execute("DROP INDEX IF EXISTS ix_deleted_tenants_due")
    for col in (
        "completed_at",
        "claimed_at",
        "result",
        "last_error",
        "attempts",
        "scheduled_for",
        "status",
    ):
        op.execute(f"ALTER TABLE deleted_tenants DROP COLUMN IF EXISTS {col}")
    op.execute("DROP POLICY IF EXISTS billing_orders_tenant_isolation ON billing_orders")
    op.execute("DROP TABLE IF EXISTS billing_orders")
