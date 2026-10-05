"""Versioned AI-Ops eval datasets (P7-3)

``ai_ops_datasets.version`` was always 1: there was no way to edit a dataset,
and a run never recorded which version it ran. Each version of a dataset is now
a row of ``ai_ops_dataset_versions`` with its own golden tasks and a status:

* ``published`` — immutable; a run always runs (and records) a published
  version. Creating a dataset publishes version 1; running a draft publishes it.
* ``draft`` — the one editable head. Editing a published head creates draft
  ``head + 1``; editing a draft head changes it in place.

``ai_ops_datasets`` keeps the head (``version``, ``golden_tasks``) for listing.
Existing datasets are backfilled as published version rows. Tenant-isolated
with ENABLE + FORCE row level security, like the other ai_ops_* tables.

Revision ID: f6a9d4e2b8c5
Revises: e5b8c3d1a7f4
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f6a9d4e2b8c5"
down_revision: str | None = "e5b8c3d1a7f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "ai_ops_dataset_versions"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            tenant_id    TEXT NOT NULL,
            dataset_id   TEXT NOT NULL,
            version      INTEGER NOT NULL CHECK (version >= 1),
            status       TEXT NOT NULL CHECK (status IN ('draft', 'published')),
            golden_tasks JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            published_at TIMESTAMPTZ,
            PRIMARY KEY (tenant_id, dataset_id, version)
        )
    """)
    # At most one draft per dataset (the editable head).
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{_TABLE}_one_draft "
        f"ON {_TABLE} (tenant_id, dataset_id) WHERE status = 'draft'"
    )
    op.execute(f"""
        INSERT INTO {_TABLE}
            (tenant_id, dataset_id, version, status, golden_tasks, created_at, published_at)
        SELECT tenant_id, id, version, 'published', golden_tasks, created_at, created_at
        FROM ai_ops_datasets
        ON CONFLICT DO NOTHING
    """)
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
