"""Migration e5c1a9d3b7f2 (durable gateway conversation mappings) vs its ORM models.

Pure import-and-inspect: no database. Pins the chain position, the ENABLE+FORCE
RLS tenant policy, the ON CONFLICT target (primary key), the cascade to
``chat_sessions`` and that the ORM model columns match the DDL.
"""

from __future__ import annotations

import importlib
import inspect
import re
from typing import Any

import pytest

from app.db.models import ChatChannelSession, ChatPrincipalSession

MODULE = "app.db.migrations.versions.e5c1a9d3b7f2_chat_channel_session_mappings"


def _mig() -> Any:
    return importlib.import_module(MODULE)


def _upgrade_sql() -> list[str]:
    calls: list[str] = []

    class _Op:
        def execute(self, sql: str) -> None:
            calls.append(" ".join(str(sql).split()))

    mig = _mig()
    original = mig.op
    mig.op = _Op()
    try:
        mig.upgrade()
    finally:
        mig.op = original
    return calls


def _downgrade_sql() -> list[str]:
    calls: list[str] = []

    class _Op:
        def execute(self, sql: str) -> None:
            calls.append(" ".join(str(sql).split()))

    mig = _mig()
    original = mig.op
    mig.op = _Op()
    try:
        mig.downgrade()
    finally:
        mig.op = original
    return calls


def _ddl_columns(sql: list[str], table: str) -> set[str]:
    [create] = [s for s in sql if s.startswith(f"CREATE TABLE IF NOT EXISTS {table} ")]
    body = create[create.index("(") + 1 : create.rindex(")")]
    cols = set()
    for part in re.split(r",\s*(?![^()]*\))", body):
        name = part.strip().split(" ", 1)[0]
        if name and name.upper() != "CONSTRAINT":
            cols.add(name)
    return cols


def test_revision_chain() -> None:
    mig = _mig()
    assert mig.revision == "e5c1a9d3b7f2"
    assert mig.down_revision == "d2b7e4f1a8c6"


@pytest.mark.parametrize("table", ["chat_channel_sessions", "chat_principal_sessions"])
def test_rls_enabled_forced_and_tenant_policy(table: str) -> None:
    sql = _upgrade_sql()
    assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
    assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql
    policy = [s for s in sql if s.startswith(f"CREATE POLICY {table}_tenant_isolation ON {table}")]
    assert len(policy) == 1
    assert "USING (tenant_id = current_setting('app.tenant_id', TRUE))" in policy[0]
    assert "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))" in policy[0]


def test_unique_key_is_the_on_conflict_target_and_sessions_cascade() -> None:
    sql = _upgrade_sql()
    joined = "\n".join(sql)
    assert "PRIMARY KEY (tenant_id, channel, channel_user_id)" in joined
    assert "PRIMARY KEY (tenant_id, principal_id)" in joined
    assert joined.count("REFERENCES chat_sessions (id) ON DELETE CASCADE") == 2

    from app.chat import repository

    src = inspect.getsource(repository)
    assert "ON CONFLICT (tenant_id, channel, channel_user_id) DO NOTHING" in src
    assert "ON CONFLICT (tenant_id, principal_id) DO NOTHING" in src


@pytest.mark.parametrize(
    ("model", "table"),
    [
        (ChatChannelSession, "chat_channel_sessions"),
        (ChatPrincipalSession, "chat_principal_sessions"),
    ],
)
def test_orm_model_matches_migration(model: Any, table: str) -> None:
    assert model.__tablename__ == table
    sql = _upgrade_sql()
    assert set(model.__table__.columns.keys()) == _ddl_columns(sql, table)
    pk = [c.name for c in model.__table__.primary_key.columns]
    assert f"PRIMARY KEY ({', '.join(pk)})" in "\n".join(sql)
    [fk] = list(model.__table__.c.chat_session_id.foreign_keys)
    assert fk.target_fullname == "chat_sessions.id"
    assert fk.ondelete == "CASCADE"


def test_downgrade_drops_policies_and_tables() -> None:
    sql = _downgrade_sql()
    for table in ("chat_channel_sessions", "chat_principal_sessions"):
        assert f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}" in sql
        assert f"DROP TABLE IF EXISTS {table}" in sql
