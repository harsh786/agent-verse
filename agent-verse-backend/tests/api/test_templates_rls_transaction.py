"""Goal-template DB paths must run entirely inside one tenant-scoped transaction.

Regression for the schema-driven cross-tenant sweep
(tests/e2e_full/test_cross_tenant_sweep_e2e.py, least-privilege mode):
``POST /templates`` answered 500 with ``InvalidRequestError: Could not refresh
instance '<GoalTemplate ...>'``. ``_create_db`` set ``app.tenant_id`` with
``set_config(..., true)`` (transaction-local) on an autobegun transaction,
committed, and THEN called ``session.refresh(obj)`` — which ran in a new
transaction with the GUC gone, so the NOBYPASSRLS role's FORCE'd RLS policy hid
the row it had just inserted. ``_update_db`` had the same shape.

``_StrictRlsSession`` models exactly the two Postgres behaviours involved:

* ``set_config('app.tenant_id', x, true)`` is transaction-local — commit or
  rollback resets it;
* under FORCE ROW LEVEL SECURITY for a NOBYPASSRLS role a row is visible /
  writable only while ``app.tenant_id`` equals its ``tenant_id``.

The real-Postgres counterpart is tests/api/test_server_errors_rls_integration.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.exc import InvalidRequestError

from app.api.templates import _TemplateStore


class _PolicyViolationError(Exception):
    pass


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _StrictRlsSession:
    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table  # id -> GoalTemplate, shared by every session ("the DB")
        self.in_txn = False
        self.guc = ""
        self._adds: list[Any] = []
        self._deletes: list[Any] = []

    # -- lifecycle --------------------------------------------------------
    async def __aenter__(self) -> _StrictRlsSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self.in_txn:
            await self.rollback()

    def begin(self) -> Any:
        session = self

        @asynccontextmanager
        async def _txn() -> Any:
            session.in_txn = True
            try:
                yield session
            except BaseException:
                await session.rollback()
                raise
            await session.commit()

        return _txn()

    def _end(self) -> None:
        self.in_txn = False
        self.guc = ""  # SET LOCAL semantics
        self._adds.clear()
        self._deletes.clear()

    async def commit(self) -> None:
        await self.flush()
        self._end()

    async def rollback(self) -> None:
        self._end()

    # -- RLS --------------------------------------------------------------
    def _check(self, tenant_id: str, what: str) -> None:
        if not self.in_txn or self.guc != tenant_id:
            raise _PolicyViolationError(
                f"{what}: row-level security (app.tenant_id={self.guc!r}, row={tenant_id!r})"
            )

    def add(self, obj: Any) -> None:
        self._adds.append(obj)

    async def delete(self, obj: Any) -> None:
        self._deletes.append(obj)

    async def flush(self) -> None:
        for obj in self._adds:
            self._check(obj.tenant_id, "INSERT")
            self.table[obj.id] = obj
        for obj in self._deletes:
            self._check(obj.tenant_id, "DELETE")
            self.table.pop(obj.id, None)
        self._adds.clear()
        self._deletes.clear()

    async def refresh(self, obj: Any) -> None:
        visible = self.in_txn and self.guc == obj.tenant_id and obj.id in self.table
        if not visible:
            raise InvalidRequestError(f"Could not refresh instance '{obj!r}'")

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        self.in_txn = True  # autobegin
        params = params or {}
        sql = str(stmt)
        if "set_config('app.tenant_id'" in sql:
            self.guc = str(params.get("tid", ""))
            return _Result([])
        if sql.lstrip().upper().startswith("INSERT INTO GOAL_TEMPLATES"):
            self._check(params["tenant_id"], "INSERT")
            if params["id"] not in self.table:
                from app.db.models.template import GoalTemplate

                self.table[params["id"]] = GoalTemplate(
                    id=params["id"],
                    tenant_id=params["tenant_id"],
                    name=params["name"],
                    description=params["description"],
                    goal_text=params["goal_text"],
                    domain=params["domain"],
                    parameters=[],
                    use_count=0,
                    version=1,
                    created_at=params["now"],
                    updated_at=params["now"],
                )
            return _Result([])
        if sql.lstrip().upper().startswith("UPDATE GOAL_TEMPLATES SET USE_COUNT"):
            row = self.table.get(params["id"])
            # RLS makes a foreign row invisible to the UPDATE (0 rows, no error).
            if row is not None and self.in_txn and self.guc == row.tenant_id:
                if params.get("tid", row.tenant_id) == row.tenant_id:
                    row.use_count += 1
            return _Result([])
        # ORM SELECT over goal_templates: RLS filter, then the bound predicates.
        bound = stmt.compile().params
        rows = [r for r in self.table.values() if self.in_txn and r.tenant_id == self.guc]
        for key, value in bound.items():
            col = key.rsplit("_", 1)[0]
            if col in ("id", "tenant_id", "domain"):
                rows = [r for r in rows if getattr(r, col) == value]
        return _Result(rows)


def _store(table: dict[str, Any], *, seed: bool = False) -> _TemplateStore:
    store = _TemplateStore(seed_builtins=seed)
    store.set_db(lambda: _StrictRlsSession(table))
    return store


async def _create(store: _TemplateStore, tenant: str, name: str = "T") -> dict[str, Any]:
    return await store.create(
        tenant_id=tenant,
        name=name,
        description="d",
        goal_text="Deploy {{svc}}",
        domain="devops",
        parameters=[{"name": "svc"}],
    )


async def test_create_under_rls_returns_and_persists_the_row() -> None:
    table: dict[str, Any] = {}
    store = _store(table)
    created = await _create(store, "tenant-a")
    assert created["tenant_id"] == "tenant-a"
    assert created["version"] == 1
    assert table[created["id"]].name == "T"
    # And it is readable back through the same RLS-scoped path.
    assert (await store.get("tenant-a", created["id"]))["name"] == "T"


async def test_update_under_rls_bumps_version_without_refresh() -> None:
    table: dict[str, Any] = {}
    store = _store(table)
    created = await _create(store, "tenant-a")
    updated = await store.update(
        tenant_id="tenant-a",
        template_id=created["id"],
        name="T2",
        description="d2",
        goal_text="Deploy {{svc}} to {{env}}",
        domain="devops",
        parameters=[{"name": "svc"}, {"name": "env"}],
    )
    assert updated is not None
    assert updated["version"] == 2 and updated["name"] == "T2"
    assert table[created["id"]].version == 2


async def test_cross_tenant_reads_and_writes_see_nothing() -> None:
    table: dict[str, Any] = {}
    store = _store(table)
    created = await _create(store, "tenant-a")
    tid = created["id"]
    assert await store.get("tenant-b", tid) is None
    assert await store.list("tenant-b") == []
    assert (
        await store.update(
            tenant_id="tenant-b",
            template_id=tid,
            name="pwned",
            description="",
            goal_text="x",
            domain="general",
            parameters=[],
        )
        is None
    )
    assert await store.delete("tenant-b", tid) is False
    await store.increment_use_count("tenant-b", tid)
    assert table[tid].name == "T" and table[tid].use_count == 0


async def test_list_increment_and_delete_under_rls() -> None:
    table: dict[str, Any] = {}
    store = _store(table)
    created = await _create(store, "tenant-a")
    await _create(store, "tenant-b", name="other")
    listed = await store.list("tenant-a", domain="devops")
    assert [t["id"] for t in listed] == [created["id"]]
    await store.increment_use_count("tenant-a", created["id"])
    assert table[created["id"]].use_count == 1
    assert await store.delete("tenant-a", created["id"]) is True
    assert created["id"] not in table


async def test_builtin_seed_under_rls_inserts_for_the_tenant() -> None:
    table: dict[str, Any] = {}
    store = _store(table, seed=True)
    listed = await store.list("tenant-seed")
    assert len(listed) >= 14
    # Served from the DB rows the seed wrote (not the in-memory fallback).
    assert len(table) == len(listed)
    assert all(row.tenant_id == "tenant-seed" for row in table.values())


async def test_the_old_commit_then_refresh_shape_is_what_failed() -> None:
    """Pin the model: the pre-fix sequence raises exactly the sweep's error."""
    from sqlalchemy import text

    from app.db.models.template import GoalTemplate

    table: dict[str, Any] = {}
    async with _StrictRlsSession(table) as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": "tenant-a"}
        )
        obj = GoalTemplate(id="t-1", tenant_id="tenant-a", name="n", goal_text="g")
        session.add(obj)
        await session.commit()  # INSERT succeeds (GUC still set during the flush)
        with pytest.raises(InvalidRequestError, match="Could not refresh instance"):
            await session.refresh(obj)  # new transaction, GUC reset -> row hidden
