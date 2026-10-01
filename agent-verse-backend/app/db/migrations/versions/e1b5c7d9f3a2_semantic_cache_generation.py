"""semantic_cache_entries: real tenant ids + a generation column (KB-51).

The semantic cache's knowledge generation used to be folded into the tenant id
(``'<tenant>#gN'``) and written into ``tenant_id VARCHAR(32)``: every durable
write failed once a tenant changed its knowledge, and real tenant ids (36-char
UUIDs) never fit at all. The generation is now its own column, ``tenant_id``
holds the real tenant (the RLS identity) and is widened to 64.

The policies on the table reference ``tenant_id``, and Postgres refuses to
change the type of a column a policy uses, so they are read from ``pg_policies``,
dropped, and recreated verbatim around the ALTER.

Revision ID: e1b5c7d9f3a2
Revises: d7e3a1f9b2c4
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "e1b5c7d9f3a2"
down_revision: str | None = "d7e3a1f9b2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "semantic_cache_entries"


def _policies() -> list[Any]:
    return list(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT policyname, permissive, cmd, qual, with_check FROM pg_policies "
                "WHERE schemaname = current_schema() AND tablename = :t"
            ),
            {"t": _TABLE},
        )
        .fetchall()
    )


def _recreate(policies: list[Any]) -> None:
    for name, permissive, cmd, qual, with_check in policies:
        sql = f'CREATE POLICY "{name}" ON {_TABLE} AS {permissive} FOR {cmd}'
        if qual:
            sql += f" USING ({qual})"
        if with_check:
            sql += f" WITH CHECK ({with_check})"
        op.execute(sql)


def _alter_tenant_id(type_sql: str) -> None:
    policies = _policies()
    for row in policies:
        op.execute(f'DROP POLICY "{row[0]}" ON {_TABLE}')
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN tenant_id TYPE {type_sql}")
    _recreate(policies)


def upgrade() -> None:
    _alter_tenant_id("VARCHAR(64)")
    op.execute(
        f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS generation BIGINT NOT NULL DEFAULT 0"
    )
    # Lookups and the purge filter (tenant_id, generation, created_at); it
    # prefix-covers the two tenant-only indexes it replaces.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_semantic_cache_tenant_gen_created "
        f"ON {_TABLE} (tenant_id, generation, created_at)"
    )
    op.execute("DROP INDEX IF EXISTS idx_semantic_cache_tenant_created")
    op.execute("DROP INDEX IF EXISTS ix_semantic_cache_tenant")


def downgrade() -> None:
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_semantic_cache_tenant ON {_TABLE} (tenant_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_semantic_cache_tenant_created "
        f"ON {_TABLE} (tenant_id, created_at)"
    )
    op.execute("DROP INDEX IF EXISTS idx_semantic_cache_tenant_gen_created")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS generation")
    # Rows whose tenant id does not fit the old width cannot be kept.
    op.execute(f"DELETE FROM {_TABLE} WHERE length(tenant_id) > 32")
    _alter_tenant_id("VARCHAR(32)")
