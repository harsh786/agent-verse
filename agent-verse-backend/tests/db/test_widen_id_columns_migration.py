"""P8b-4: no id-carrying column is narrower than a dashed UUID.

P4-2 widened only the audit/event tables; ~95 other ``tenant_id`` columns (and
the ``id`` / ``*_id`` columns carrying the same identifiers) stayed
``VARCHAR(32)``, so a dashed 36-char tenant id overflowed them. Migration
e7b1c4d9a2f6 widens all of them to 64 (dropping and recreating the RLS policies
and views in the way), and must downgrade and re-upgrade cleanly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from typing import Any

import pytest

from tests._test_backends import BACKEND_ROOT, migrated_postgres

pytestmark = pytest.mark.integration

_PREV = "f6a9d4e2b8c5"
# The widening migration itself. Policies / views are compared AT this revision:
# later migrations add their own (e.g. the chat owner policies), which a
# downgrade to _PREV correctly removes, so a head snapshot can never match.
_WIDEN = "e7b1c4d9a2f6"
_DASHED_TENANT = str(uuid.uuid4())  # 36 chars
assert len(_DASHED_TENANT) == 36

_NARROW_IDS = """
SELECT c.table_name, c.column_name, c.character_maximum_length
FROM information_schema.columns c
JOIN pg_class k ON k.relname = c.table_name AND k.relnamespace = current_schema()::regnamespace
WHERE c.table_schema = current_schema() AND c.data_type = 'character varying'
  AND (c.column_name = 'tenant_id' AND c.character_maximum_length < 64
       OR (c.column_name = 'id' OR c.column_name LIKE '%\\_id')
          AND c.character_maximum_length < 36)
  AND k.relkind IN ('r', 'p')
ORDER BY 1, 2
"""

_POLICIES = (
    "SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check "
    "FROM pg_policies WHERE schemaname = current_schema()"
)
_VIEWS = (
    "SELECT c.relname, pg_get_viewdef(c.oid), c.relacl::text FROM pg_class c "
    "WHERE c.relkind = 'v' AND c.relnamespace = current_schema()::regnamespace"
)

# A sample of the tables P4-2 left at 32 (named in the finding) plus core ones.
_SAMPLE = (
    "approval_requests",
    "goal_fanout_ledger",
    "progress_ledger_revisions",
    "goals",
    "agents",
    "guardrail_rules",
    "chat_message_usage",
)


def _alembic(url: str, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": url, "ENVIRONMENT": "development"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, check=False, timeout=600,
    )
    assert result.returncode == 0, f"alembic {args}:\n{result.stdout}\n{result.stderr}"


def _dummy(type_name: str, column: str) -> str:
    """A text literal for one required column (cast to its type in SQL)."""
    if column in ("tenant_id", "id"):
        return _DASHED_TENANT
    if type_name.startswith(("character varying", "text", "character")):
        return f"{column[:8]}-{uuid.uuid4().hex[:8]}"
    if type_name in ("integer", "bigint", "smallint", "real", "double precision") or (
        type_name.startswith("numeric")
    ):
        return "0"
    if type_name == "boolean":
        return "false"
    if type_name in ("json", "jsonb"):
        return "{}"
    if type_name.startswith(("timestamp", "date")):
        return "2026-10-06T00:00:00+00:00"
    if type_name == "uuid":
        return str(uuid.uuid4())
    if type_name.endswith("[]"):
        return "{}"
    if type_name.startswith("vector"):
        dims = type_name[type_name.find("(") + 1 : type_name.find(")")] if "(" in type_name else "3"
        return "[" + ",".join(["0"] * int(dims)) + "]"
    raise AssertionError(f"no dummy for {type_name} ({column})")


async def _insert_dashed_row(conn: Any, table: str) -> None:
    """One row whose tenant_id (and id) is a dashed 36-char UUID; every other
    required column gets a plain value (FK/trigger checks are off: this probes
    column widths)."""
    cols = await conn.fetch(
        "SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS type_name "
        "FROM pg_attribute a WHERE a.attrelid = to_regclass($1) AND a.attnum > 0 "
        "AND NOT a.attisdropped AND a.attgenerated = '' "
        "AND (a.attname IN ('tenant_id', 'id') OR (a.attnotnull AND NOT a.atthasdef))",
        f'"{table}"',
    )
    names = [f'"{c["attname"]}"' for c in cols]
    params = ", ".join(f"CAST(${i + 1} AS text)::{c['type_name']}" for i, c in enumerate(cols))
    await conn.execute(
        f'INSERT INTO "{table}" ({", ".join(names)}) VALUES ({params})',
        *[_dummy(c["type_name"], c["attname"]) for c in cols],
    )


async def test_widened_ids_round_trip_and_hold_a_dashed_tenant() -> None:
    import asyncpg

    with migrated_postgres() as url:
        # Step back from head to the widening revision (exercises every later
        # migration's downgrade too) and snapshot the schema there.
        _alembic(url, "downgrade", _WIDEN)
        dsn = url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetch(_NARROW_IDS) == []
            policies = {tuple(r) for r in await conn.fetch(_POLICIES)}
            views = {tuple(r) for r in await conn.fetch(_VIEWS)}

            # A dashed 36-char tenant id fits every sampled table.
            await conn.execute("SET session_replication_role = replica")
            txn = conn.transaction()
            await txn.start()
            try:
                for table in _SAMPLE:
                    await _insert_dashed_row(conn, table)
                    stored = await conn.fetchval(
                        f'SELECT count(*) FROM "{table}" WHERE tenant_id = $1', _DASHED_TENANT
                    )
                    assert stored == 1, table
            finally:
                await txn.rollback()  # leave no rows: the downgrade must be able to narrow
        finally:
            await conn.close()

        _alembic(url, "downgrade", _PREV)
        conn = await asyncpg.connect(dsn)
        try:
            narrowed = await conn.fetch(_NARROW_IDS)
            assert len(narrowed) > 200  # back to VARCHAR(32)
            assert {tuple(r) for r in await conn.fetch(_POLICIES)} == policies
            assert {tuple(r) for r in await conn.fetch(_VIEWS)} == views
        finally:
            await conn.close()

        _alembic(url, "upgrade", _WIDEN)
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetch(_NARROW_IDS) == []
            assert {tuple(r) for r in await conn.fetch(_POLICIES)} == policies
            assert {tuple(r) for r in await conn.fetch(_VIEWS)} == views
        finally:
            await conn.close()

        # And forward to head again: the later migrations re-apply cleanly.
        _alembic(url, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetch(_NARROW_IDS) == []
        finally:
            await conn.close()
