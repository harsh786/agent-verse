"""Ingestion DLQ entries that belong to no Source (single-URL / repository ingest).

``ingestion_dlq.source_id`` was NOT NULL with a foreign key to
``source_configs``, so only a Source's documents could be dead-lettered. A failed
``POST /knowledge/ingest/url`` was lost, and the repository ingest's entries
(``source_id = 'repo:<url>'``) violated the FK and were never written.

* ``source_id`` becomes nullable (the FK stays: a Source's entries still cascade
  with it). A source-less entry carries ``source_id`` NULL and a ``raw_doc_json``
  payload with a ``kind`` (``url`` / ``repository``) the retry job replays.
* ``ux_ingestion_dlq_open_sourceless (tenant_id, doc_id) WHERE source_id IS NULL
  AND resolved_at IS NULL AND permanent_failure IS NOT TRUE``: at most one open
  source-less entry per document, so posting the same failing URL again updates
  that entry (``INSERT .. ON CONFLICT``) instead of adding a row.

Downgrade deletes the source-less rows (they cannot satisfy NOT NULL).

Revision ID: c3d5e7f9a1b2
Revises: b8d0f2a4c6e7
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c3d5e7f9a1b2"
down_revision: str | None = "b8d0f2a4c6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE ingestion_dlq ALTER COLUMN source_id DROP NOT NULL")
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_ingestion_dlq_open_sourceless "
            "ON ingestion_dlq (tenant_id, doc_id) "
            "WHERE source_id IS NULL AND resolved_at IS NULL "
            "AND permanent_failure IS NOT TRUE"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ux_ingestion_dlq_open_sourceless")
    op.execute("DELETE FROM ingestion_dlq WHERE source_id IS NULL")
    op.execute("ALTER TABLE ingestion_dlq ALTER COLUMN source_id SET NOT NULL")
