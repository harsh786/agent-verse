"""Drop the orphaned tables routing_decisions and routing_outcomes (a10-F251-01/02).

Their only reader/writer, ``app/routing_runtime`` (Program 10's "canonical routing
runtime"), was never on any request or goal path and was deleted on the owner's
decision; live routing is ``app/agent/model_router.py``, agent auto-routing and
``app/orchestration/pattern_selector.py``.

No data is lost silently: ``upgrade`` refuses (and changes nothing) while either
table still holds a row, unless ``AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP=1`` is set (the
guard of e5f1a9c3d7b2, which drops the orphaned experiment tables). Rows are counted
with FORCE ROW LEVEL SECURITY lifted inside this migration's transaction, so a
migration role that owns the tables but is not a superuser / BYPASSRLS still sees
every tenant's rows (with FORCE on it would see none and pass the guard). If the
guard refuses, the transaction rolls back and FORCE is restored with it.

``downgrade`` recreates both tables as they were at the previous head (0103, with
the id columns widened by e7b1c4d9a2f6): check / unique / foreign keys, indexes,
ENABLE + FORCE row level security and the tenant isolation policies. The app role's
grants come back from ``ensure_app_role``, which ``env.py`` runs after every migration.

Revision ID: b3e7d1f9a5c2
Revises: a8d2f6c4e1b9
Create Date: 2026-10-07
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e7d1f9a5c2"
down_revision: str | Sequence[str] | None = "a8d2f6c4e1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALLOW_ENV = "AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP"

# Drop order: routing_outcomes references routing_decisions.
ORPHAN_TABLES: tuple[str, ...] = ("routing_outcomes", "routing_decisions")

_GUC = "current_setting('app.tenant_id', true)"


class OrphanTableNotEmptyError(RuntimeError):
    """An orphaned table still holds rows and the drop was not explicitly allowed."""


def _drop_allowed() -> bool:
    return os.environ.get(ALLOW_ENV, "").strip().lower() in {"1", "true", "yes"}


def _row_counts(bind: sa.engine.Connection) -> dict[str, int]:
    """Row count of every orphaned table that still exists, across all tenants."""
    counts: dict[str, int] = {}
    for table in ORPHAN_TABLES:
        exists = bind.execute(sa.text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table})
        if not exists.scalar_one():
            continue
        # FORCE RLS binds the table owner too; lift it (rolled back with the
        # transaction if the guard refuses) so the count is not tenant-filtered.
        bind.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        counts[table] = int(bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
    return counts


def upgrade() -> None:
    bind = op.get_bind()
    counts = _row_counts(bind)
    non_empty = {table: n for table, n in counts.items() if n > 0}
    if non_empty and not _drop_allowed():
        listing = ", ".join(f"{table} ({n} rows)" for table, n in non_empty.items())
        raise OrphanTableNotEmptyError(
            f"Refusing to drop orphaned tables that still hold data: {listing}. "
            "No code reads or writes them any more (app/routing_runtime was removed), "
            "but dropping them deletes those rows permanently. Back them up if you "
            f"need them, then re-run the migration with {ALLOW_ENV}=1 to drop them anyway."
        )
    for table in counts:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        f"USING (tenant_id = {_GUC}) WITH CHECK (tenant_id = {_GUC})"
    )


def downgrade() -> None:
    # As created by 0103_routing_safety_optimization, widened by e7b1c4d9a2f6.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS routing_decisions (
            id                          VARCHAR(64) NOT NULL,
            tenant_id                   VARCHAR(64) NOT NULL,
            goal_id                     VARCHAR(64) NOT NULL,
            execution_id                VARCHAR(64) NOT NULL,
            category                    VARCHAR(20) NOT NULL,
            profile_version             INTEGER NOT NULL,
            selected_candidate_id       TEXT,
            selected_candidate_version  TEXT,
            safe_rationale              TEXT NOT NULL,
            payload                     JSONB NOT NULL,
            created_at                  TIMESTAMPTZ DEFAULT now() NOT NULL,
            CONSTRAINT routing_decisions_pkey PRIMARY KEY (id),
            CONSTRAINT routing_decisions_tenant_id_fkey FOREIGN KEY (tenant_id)
                REFERENCES tenants(id) ON DELETE CASCADE,
            CONSTRAINT ck_routing_decision_category
                CHECK (category IN ('model','skill','tool','embedding','strategy')),
            CONSTRAINT uq_routing_decision_execution_category_id
                UNIQUE (tenant_id, execution_id, category, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_routing_decisions_tenant_goal_created "
        "ON routing_decisions (tenant_id, goal_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_routing_decisions_tenant_category_created "
        "ON routing_decisions (tenant_id, category, created_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS routing_outcomes (
            id                 VARCHAR(64) NOT NULL,
            tenant_id          VARCHAR(64) NOT NULL,
            decision_id        VARCHAR(64) NOT NULL,
            attempt            INTEGER NOT NULL,
            evaluator_version  VARCHAR(64) NOT NULL,
            success            BOOLEAN NOT NULL,
            quality_score      INTEGER NOT NULL,
            actual_cost_usd    NUMERIC(18, 6) NOT NULL,
            actual_latency_ms  INTEGER NOT NULL,
            prompt_tokens      INTEGER NOT NULL,
            completion_tokens  INTEGER NOT NULL,
            fallback_used      BOOLEAN NOT NULL,
            error_class        TEXT,
            payload            JSONB NOT NULL,
            recorded_at        TIMESTAMPTZ DEFAULT now() NOT NULL,
            CONSTRAINT routing_outcomes_pkey PRIMARY KEY (id),
            CONSTRAINT routing_outcomes_tenant_id_fkey FOREIGN KEY (tenant_id)
                REFERENCES tenants(id) ON DELETE CASCADE,
            CONSTRAINT routing_outcomes_decision_id_fkey FOREIGN KEY (decision_id)
                REFERENCES routing_decisions(id) ON DELETE CASCADE,
            CONSTRAINT ck_routing_outcome_attempt CHECK (attempt > 0),
            CONSTRAINT ck_routing_outcome_quality CHECK (quality_score BETWEEN 0 AND 10000),
            CONSTRAINT ck_routing_outcome_cost CHECK (actual_cost_usd >= 0),
            CONSTRAINT ck_routing_outcome_latency CHECK (actual_latency_ms >= 0),
            CONSTRAINT ck_routing_outcome_tokens
                CHECK (prompt_tokens >= 0 AND completion_tokens >= 0),
            CONSTRAINT uq_routing_outcome_attempt_evaluator
                UNIQUE (tenant_id, decision_id, attempt, evaluator_version)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_routing_outcomes_tenant_decision_recorded "
        "ON routing_outcomes (tenant_id, decision_id, recorded_at)"
    )
    for table in ("routing_decisions", "routing_outcomes"):
        _rls(table)
