"""ORM foreign keys match the migrated schema (model/DB drift guard).

Alembic has no autogenerate here (``target_metadata = None``), so a model can
declare a constraint no migration creates. ``ChatSession.folder_id`` did: the ORM
said ``ForeignKey(chat_session_folders.id, ON DELETE SET NULL)`` while the schema
deliberately has none (f3a9c1e7d5b4 — the repository unfiles in the deleting
transaction). This compares, for every ORM table that exists in the migrated
database, the foreign keys the model declares with the ones Postgres has
(constrained columns, referred table/columns, ON DELETE): the chat tables must
match exactly, and any drift elsewhere must already be in ``_KNOWN_DRIFT``. It
also checks the length and nullability of ``chat_sessions.folder_id``.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/db/test_orm_schema_fk_drift_integration.py -m integration
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from sqlalchemy import Table, inspect
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

# Modules outside app/db/models that declare ORM tables on the shared Base.
_EXTRA_MODEL_MODULES = ("app.chat.models", "app.org.models", "app.tenancy.sub_tenants")

FkKey = tuple[str, tuple[str, ...], str, tuple[str, ...], str]

# Drift that predates this guard, outside the chat tables (2026-10-06). Frozen so
# any NEW drift fails; shrink it as each table is reconciled, never grow it.
_KNOWN_DRIFT: frozenset[tuple[str, FkKey]] = frozenset(
    {
        ("orm_only", ("knowledge_collections", ("tenant_id",), "tenants", ("id",), "CASCADE")),
        ("orm_only", ("documents", ("collection_id",), "knowledge_collections", ("id",), "CASCADE")),
        ("orm_only", ("tenant_memberships", ("user_id",), "users", ("id",), "CASCADE")),
        ("db_only", ("api_keys", ("rotated_from",), "api_keys", ("id",), "NO ACTION")),
        *(
            (
                "db_only",
                (
                    f"knowledge_chunks_{dim}",
                    ("ingestion_job_id", "tenant_id", "collection_id"),
                    "knowledge_documents",
                    ("id", "tenant_id", "collection_id"),
                    "NO ACTION",
                ),
            )
            for dim in (768, 1024, 1536, 3072)
        ),
        # org_* : the ORM omits ON DELETE CASCADE that the schema has.
        *(
            (side, (table, (col,), referred, ("id",), action))
            for table, col, referred in (
                ("org_departments", "org_id", "organizations"),
                ("org_teams", "org_id", "organizations"),
                ("org_missions", "org_id", "organizations"),
                ("org_workstreams", "org_id", "organizations"),
                ("org_workstreams", "mission_id", "org_missions"),
                ("org_tasks", "org_id", "organizations"),
                ("org_tasks", "mission_id", "org_missions"),
                ("org_decisions", "org_id", "organizations"),
                ("org_events", "org_id", "organizations"),
            )
            for side, action in (("orm_only", "NO ACTION"), ("db_only", "CASCADE"))
        ),
    }
)


def _orm_tables() -> dict[str, Table]:
    import pkgutil

    import app.db.models as models_pkg
    from app.db.models import Base

    for mod in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"app.db.models.{mod.name}")
    for name in _EXTRA_MODEL_MODULES:
        importlib.import_module(name)
    return dict(Base.metadata.tables)


def _norm_ondelete(value: Any) -> str:
    text = (value or "NO ACTION").upper()
    return "NO ACTION" if text == "RESTRICT_DEFAULT" else text


def _orm_fks(table: Table) -> set[FkKey]:
    out: set[FkKey] = set()
    for fk in table.foreign_key_constraints:
        cols = tuple(c.name for c in fk.columns)
        referred = fk.elements[0].column.table.name
        referred_cols = tuple(e.column.name for e in fk.elements)
        out.add((table.name, cols, referred, referred_cols, _norm_ondelete(fk.ondelete)))
    return out


async def test_orm_foreign_keys_match_the_migrated_schema(pg_url: str) -> None:
    tables = _orm_tables()
    engine = create_async_engine(pg_url)
    try:
        async with engine.connect() as conn:

            def _reflect(sync_conn: Any) -> dict[str, Any]:
                insp = inspect(sync_conn)
                existing = set(insp.get_table_names())
                result: dict[str, Any] = {"fks": {}, "folder_col": None}
                for name in tables:
                    if name not in existing:
                        continue
                    db_fks: set[FkKey] = set()
                    for fk in insp.get_foreign_keys(name):
                        db_fks.add(
                            (
                                name,
                                tuple(fk["constrained_columns"]),
                                fk["referred_table"],
                                tuple(fk["referred_columns"]),
                                _norm_ondelete((fk.get("options") or {}).get("ondelete")),
                            )
                        )
                    result["fks"][name] = db_fks
                for col in insp.get_columns("chat_sessions"):
                    if col["name"] == "folder_id":
                        result["folder_col"] = col
                return result

            reflected = await conn.run_sync(_reflect)
    finally:
        await engine.dispose()

    assert "chat_sessions" in reflected["fks"], "chat tables missing from the migrated schema"
    drift: set[tuple[str, FkKey]] = set()
    for name, db_fks in reflected["fks"].items():
        orm_fks = _orm_fks(tables[name])
        drift |= {("orm_only", fk) for fk in orm_fks - db_fks}
        drift |= {("db_only", fk) for fk in db_fks - orm_fks}

    chat_drift = sorted(d for d in drift if d[1][0].startswith("chat_"))
    assert not chat_drift, "chat ORM/schema foreign-key drift:\n" + "\n".join(map(str, chat_drift))
    new_drift = sorted(drift - _KNOWN_DRIFT)
    assert not new_drift, (
        "new ORM/schema foreign-key drift (fix the model or add a migration):\n"
        + "\n".join(map(str, new_drift))
    )

    folder_col = reflected["folder_col"]
    assert folder_col is not None
    assert folder_col["nullable"] is True
    orm_col = tables["chat_sessions"].c.folder_id
    assert orm_col.nullable is True
    assert getattr(folder_col["type"], "length", None) == orm_col.type.length  # type: ignore[attr-defined]
