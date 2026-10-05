"""Widen audit/event tenant and id columns that are narrower than a UUID (P4-2)

``audit_log.tenant_id`` (and the event/outbox tables) were ``VARCHAR(32)``: the
32-char hex form tenants are created with. A dashed UUID (36 chars) — which a
resumed workflow run carries, and which ``tenants.id`` (``VARCHAR(36)``) admits —
overflowed the column, the audit insert raised
``StringDataRightTruncationError`` and the governed code step failed with
"execution could not be audited" (live TRIGGER-CHAIN).

Every ``tenant_id`` and ``id`` column of the audit/event tables is widened to
``VARCHAR(64)`` (the width ``audit_log.goal_id`` already has). Raising a varchar
limit is a catalog-only change in Postgres (no table rewrite, no index rebuild).
Postgres refuses to change the type of a column an RLS policy references, so the
policies of each table (and of every ``goal_events`` partition) are captured
from ``pg_policies``, dropped, and recreated verbatim around the ALTER.

``goal_events.goal_id`` stays ``VARCHAR(32)``: it is a foreign key to
``goals.id`` (``VARCHAR(32)``), so it can never hold anything longer.

Revision ID: d4e7a2c9b1f3
Revises: c3f9a1d7e5b2
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "d4e7a2c9b1f3"
down_revision: str | None = "c3f9a1d7e5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[str, ...] = (
    "audit_log",
    "goal_events",
    "event_inbox",
    "coordination_events",
    "coordination_outbox",
    "coordination_dead_letters",
)
_COLUMNS: tuple[str, ...] = ("id", "tenant_id")
_WIDE = 64
_NARROW = 32


def _quote(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _tables_with_partitions(bind: Any, table: str) -> list[str]:
    rows = bind.execute(
        sa.text(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = :t"
        ),
        {"t": table},
    ).scalars()
    return [table, *rows]


def _policies(bind: Any, tables: list[str]) -> list[dict[str, Any]]:
    rows = bind.execute(
        sa.text(
            "SELECT tablename, policyname, permissive, roles, cmd, qual, with_check "
            "FROM pg_policies WHERE schemaname = current_schema() "
            "AND tablename = ANY(:tables)"
        ),
        {"tables": tables},
    ).mappings()
    return [dict(r) for r in rows]


def _create_policy_sql(p: dict[str, Any]) -> str:
    roles = list(p["roles"] or ["public"])
    to = ", ".join("public" if r == "public" else _quote(r) for r in roles)
    sql = (
        f"CREATE POLICY {_quote(p['policyname'])} ON {_quote(p['tablename'])} "
        f"AS {p['permissive']} FOR {p['cmd']} TO {to}"
    )
    if p["qual"] is not None:
        sql += f" USING ({p['qual']})"
    if p["with_check"] is not None:
        sql += f" WITH CHECK ({p['with_check']})"
    return sql


def _existing_columns(bind: Any, table: str) -> set[str]:
    rows = bind.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :t"
        ),
        {"t": table},
    ).scalars()
    return set(rows)


def _resize(width: int) -> None:
    bind = op.get_bind()
    for table in _TABLES:
        cols = [c for c in _COLUMNS if c in _existing_columns(bind, table)]
        if not cols:
            continue
        family = _tables_with_partitions(bind, table)
        policies = _policies(bind, family)
        for p in policies:
            op.execute(f"DROP POLICY {_quote(p['policyname'])} ON {_quote(p['tablename'])}")
        for col in cols:
            # ALTER on a partitioned parent recurses to every partition.
            op.execute(
                f"ALTER TABLE {_quote(table)} ALTER COLUMN {_quote(col)} "
                f"TYPE VARCHAR({width})"
            )
        for p in policies:
            op.execute(_create_policy_sql(p))


def upgrade() -> None:
    _resize(_WIDE)


def downgrade() -> None:
    # Fails loudly (StringDataRightTruncationError) if a row already holds a value
    # longer than 32 chars — never truncates.
    _resize(_NARROW)
