"""A fake async session factory that models Postgres' transaction-local GUC.

Unit tests use it to prove a store runs its SQL *under the tenant GUC* that
``app.db.rls.sqlalchemy_rls_context`` sets — the property a NOBYPASSRLS
production role depends on — without a real database.

Semantics modelled (matching Postgres + SQLAlchemy AsyncSession):

* ``SELECT set_config('app.tenant_id', :tid, true)`` sets the GUC for the
  current transaction only (``is_local = true``).
* A transaction starts on ``session.begin()`` or implicitly on the first
  ``execute`` (autobegin). It ends on ``commit()``, on leaving the ``begin()``
  block, or when the session closes — and ending it clears the GUC.
* ``SET LOCAL row_security = off`` (``system_session``) is recorded as a
  maintenance-role escalation so tests can assert it never happens on a
  tenant path.

Every other statement is recorded with the GUC value in force when it ran.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RecordedStatement:
    sql: str
    params: dict[str, Any]
    tenant_guc: str | None
    explicit_txn: bool


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    @property
    def rowcount(self) -> int:
        return len(self._rows)


class _Txn:
    def __init__(self, session: RlsRecordingSession) -> None:
        self._session = session

    async def __aenter__(self) -> _Txn:
        self._session._begin(explicit=True)
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self._session._end()
        return False


@dataclass
class RlsRecordingDb:
    """Callable session factory; every session shares one statement log."""

    rows_for: Callable[[str, dict[str, Any]], list[Any]] = lambda _sql, _p: []
    statements: list[RecordedStatement] = field(default_factory=list)
    escalations: int = 0

    def __call__(self) -> RlsRecordingSession:
        return RlsRecordingSession(self)

    def touching(self, table: str) -> list[RecordedStatement]:
        return [s for s in self.statements if table in s.sql]


class RlsRecordingSession:
    def __init__(self, db: RlsRecordingDb) -> None:
        self._db = db
        self._in_txn = False
        self._explicit = False
        self._guc: str | None = None

    async def __aenter__(self) -> RlsRecordingSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self._end()
        return False

    def begin(self) -> _Txn:
        return _Txn(self)

    def _begin(self, *, explicit: bool) -> None:
        self._in_txn = True
        self._explicit = explicit

    def _end(self) -> None:
        self._in_txn = False
        self._explicit = False
        self._guc = None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        if not self._in_txn:
            self._begin(explicit=False)  # autobegin
        sql = " ".join(str(stmt).split())
        bound = dict(params or {})
        if "set_config('app.tenant_id'" in sql:
            self._guc = str(bound.get("tid", "")) or None
            return _Result([])
        if "row_security" in sql:
            self._db.escalations += 1
            return _Result([])
        self._db.statements.append(RecordedStatement(sql, bound, self._guc, self._explicit))
        return _Result(self._db.rows_for(sql, bound))

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self._end()

    async def rollback(self) -> None:
        self._end()


def assert_tenant_scoped(
    db: RlsRecordingDb, table: str, tenant_id: str, *, min_statements: int = 1
) -> list[RecordedStatement]:
    """Every statement on ``table`` ran under ``tenant_id``'s GUC and named it.

    Returns the matching statements for further assertions.
    """
    stmts = db.touching(table)
    assert len(stmts) >= min_statements, f"expected SQL on {table}, got {db.statements!r}"
    for s in stmts:
        assert s.tenant_guc == tenant_id, f"{table} statement ran without tenant GUC: {s.sql}"
        assert "tenant_id" in s.sql, f"{table} statement lacks a tenant_id predicate: {s.sql}"
        assert tenant_id in s.params.values(), f"{table} statement not bound to tenant: {s}"
    assert db.escalations == 0, "tenant path escalated to row_security = off"
    return stmts
