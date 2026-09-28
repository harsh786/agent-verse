"""A minimal AsyncSession stand-in that records SQL, for RLS-shape unit tests.

``sqlalchemy_rls_context`` issues a leading ``SELECT set_config('app.tenant_id',
...)`` and a trailing reset; they are recorded separately in ``guc_calls`` so
tests can assert both "the GUC was set to the right tenant before any data
statement" and the data statements themselves.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows
        self.rowcount = len(rows)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class _Tx:
    def __init__(self, session: RecordingSession) -> None:
        self._session = session

    async def __aenter__(self) -> _Tx:
        self._session.began += 1
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class RecordingSession:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []
        # [(sql, params)] for data statements; GUC calls go to guc_calls.
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.guc_calls: list[str] = []
        # Interleaved order of everything, to assert GUC-before-data.
        self.log: list[str] = []
        self.began = 0
        self.added: list[Any] = []
        self.flushed = 0

    async def __aenter__(self) -> RecordingSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self) -> _Tx:
        return _Tx(self)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        if "set_config('app.tenant_id'" in sql:
            tid = (params or {}).get("tid", "")
            self.guc_calls.append(tid)
            self.log.append(f"guc:{tid}")
            return _Result([])
        self.statements.append((sql, dict(params or {})))
        self.log.append("sql")
        return _Result(self.rows)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed += 1

    async def refresh(self, obj: Any) -> None:  # pragma: no cover - must not be called
        raise AssertionError("refresh() after commit runs without the tenant GUC")

    async def commit(self) -> None:  # pragma: no cover - begin() owns the commit
        raise AssertionError("explicit commit() inside session.begin()")


def factory_for(session: RecordingSession) -> Callable[[], RecordingSession]:
    return lambda: session
