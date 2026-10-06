"""Full-text search indexes hyphenated codes ("TJ-5531") as whole searchable tokens.

Postgres' default parser reads ``TJ-5531`` as the word ``tj`` plus the signed
integer ``-5531``, so a full-text query for the bare number ``5531`` (or the
spaced ``TJ 5531``, or the joined ``TJ5531``) never matched the chunk; only the
trigram / BM25 / phrase legs caught it.

* ``knowledge_fts_vector(content)``: ``to_tsvector('english', content)`` plus,
  for every ``<alnum>-<digits>`` code in the text (Unicode hyphens/dashes
  included), the ``simple`` tokens of its prefix, its number and the joined
  form: ``TJ-5531`` adds ``tj``, ``5531`` and ``tj5531``. IMMUTABLE, so it can
  back an expression index; plpgsql, so the planner never inlines it and the
  query expression always matches the index expression.
* ``idx_<table>_fts_codes`` (GIN on that expression) replaces the plain
  ``to_tsvector('english', content)`` GIN index (``idx_<table>_fts``, or the
  auto-named copy 0121 cloned onto the 2048 table) on every
  ``knowledge_chunks_<dim>`` table, built CONCURRENTLY.

Revision ID: d4e6f8a0b2c3
Revises: c3d5e7f9a1b2
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e6f8a0b2c3"
down_revision: str | None = "c3d5e7f9a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every knowledge chunk table (0091's four + 0121's 2048, cloned from 1024 with
# an auto-named copy of the FTS index).
_DIMENSIONS = (768, 1024, 1536, 2048, 3072)
# ASCII hyphen-minus first (a literal), then U+2010..U+2014 and U+2212.
_CODE_RE = "([[:alnum:]]+)[-\u2010-\u2014\u2212]([[:digit:]]+)"

_FUNCTION = f"""
CREATE OR REPLACE FUNCTION knowledge_fts_vector(content text)
RETURNS tsvector
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
    codes text;
BEGIN
    SELECT string_agg(m[1] || ' ' || m[2] || ' ' || m[1] || m[2], ' ')
      INTO codes
      FROM regexp_matches(coalesce(content, ''), '{_CODE_RE}', 'g') AS m;
    RETURN to_tsvector('english'::regconfig, coalesce(content, ''))
        || to_tsvector('simple'::regconfig, coalesce(codes, ''));
END
$fn$
"""


def _index_is_valid(name: str) -> bool | None:
    return (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT index.indisvalid FROM pg_index AS index "
                "JOIN pg_class AS relation ON relation.oid = index.indexrelid "
                "WHERE relation.relname = :name"
            ),
            {"name": name},
        )
        .scalar_one_or_none()
    )


def _create_index_concurrently(name: str, statement: str) -> None:
    """Retry an interrupted concurrent build without replacing a valid index."""
    if _index_is_valid(name) is False:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute(statement)


def _table_exists(table: str) -> bool:
    return bool(
        op.get_bind().execute(sa.text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}).scalar()
    )


def _old_fts_indexes(table: str) -> list[str]:
    """The table's GIN indexes on the plain ``to_tsvector('english', content)``."""
    rows = op.get_bind().execute(
        sa.text(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
            "AND tablename = :t AND indexdef ILIKE '%gin%' "
            "AND indexdef ILIKE '%to_tsvector(%english%content)%' "
            "AND indexdef NOT ILIKE '%knowledge_fts_vector%'"
        ),
        {"t": table},
    )
    return [str(row[0]) for row in rows]


def upgrade() -> None:
    op.execute(_FUNCTION)
    with op.get_context().autocommit_block():
        for dimension in _DIMENSIONS:
            table = f"knowledge_chunks_{dimension}"
            if not _table_exists(table):
                continue
            _create_index_concurrently(
                f"idx_{table}_fts_codes",
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{table}_fts_codes "
                f"ON {table} USING gin (knowledge_fts_vector(content))",
            )
            for name in _old_fts_indexes(table):
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for dimension in _DIMENSIONS:
            table = f"knowledge_chunks_{dimension}"
            if not _table_exists(table):
                continue
            _create_index_concurrently(
                f"idx_{table}_fts",
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{table}_fts "
                f"ON {table} USING gin (to_tsvector('english', content))",
            )
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS idx_{table}_fts_codes")
    op.execute("DROP FUNCTION IF EXISTS knowledge_fts_vector(text)")
