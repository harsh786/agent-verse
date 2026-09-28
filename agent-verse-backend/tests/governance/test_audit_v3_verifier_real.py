"""audit_v3.HashChainVerifier must verify the real audit_events chain.

It SELECTed v3 columns that do not exist (entry_hash, event_timestamp,
sequence_num), swallowed the error, fell back to an unused module singleton and
answered ``verified=True`` with 0 events — fake success on a tamper check.
The AuditWriter/AuditFlusher "shims" were no-ops, so nothing reached the chain.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.governance import audit_v3
from app.governance.audit_v3 import AuditChainVerificationError, HashChainVerifier


def _v2() -> Any:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from app.governance import audit_v2
    return audit_v2


def _chain(tenant: str, n: int) -> list[SimpleNamespace]:
    AuditEvent = _v2().AuditEvent
    base = datetime(2026, 9, 1, tzinfo=UTC)
    rows, prev = [], ""
    for i in range(n):
        ts = base + timedelta(minutes=i)
        ev = AuditEvent(
            id=f"e{i}", tenant_id=tenant, event_type="agent.updated", resource_id=f"r{i}",
            action="update", status="success", created_at=ts.isoformat(),
        )
        h = ev.compute_hash(prev)
        rows.append(SimpleNamespace(
            id=ev.id, event_type=ev.event_type, resource_id=ev.resource_id, action=ev.action,
            status=ev.status, created_at=ts, prev_hash=prev, event_hash=h,
        ))
        prev = h
    return rows


class _Result:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self) -> list[Any]:
        return self._rows

    def scalar(self) -> Any:
        return self._scalar


class _Session:
    def __init__(self, rows: list[Any], anchor_exists: bool = True) -> None:
        self.rows = rows
        self.anchor_exists = anchor_exists
        self.sql: list[str] = []

    async def execute(self, q: Any, params: Any = None) -> _Result:
        sql = str(q)
        self.sql.append(sql)
        if "set_config" in sql:
            return _Result()
        if "SELECT 1 FROM audit_events" in sql:
            return _Result(scalar=1 if self.anchor_exists else None)
        return _Result(self.rows)


_FROM = datetime(2026, 1, 1, tzinfo=UTC)
_TO = datetime(2027, 1, 1, tzinfo=UTC)


async def test_intact_chain_verifies_all_events() -> None:
    s = _Session(_chain("t1", 4))
    out = await HashChainVerifier().verify(s, "t1", _FROM, _TO)
    assert out["verified"] is True
    assert out["verified_events"] == 4
    # Scoped to the tenant under RLS, against the real columns.
    assert any("set_config('app.tenant_id'" in q for q in s.sql)
    select = next(q for q in s.sql if "FROM audit_events" in q and "event_hash" in q)
    assert "entry_hash" not in select and "sequence_num" not in select


async def test_tampered_row_is_detected() -> None:
    rows = _chain("t1", 4)
    rows[2].action = "delete"  # modified after write
    out = await HashChainVerifier().verify(_Session(rows), "t1", _FROM, _TO)
    assert out["verified"] is False
    assert out["broken_chain_at"] == "e2"


async def test_deleted_row_is_detected() -> None:
    rows = _chain("t1", 4)
    del rows[1]
    out = await HashChainVerifier().verify(_Session(rows), "t1", _FROM, _TO)
    assert out["verified"] is False


async def test_window_starting_mid_chain_needs_existing_anchor() -> None:
    rows = _chain("t1", 5)[2:]
    ok = await HashChainVerifier().verify(_Session(rows, anchor_exists=True), "t1", _FROM, _TO)
    assert ok["verified"] is True
    bad = await HashChainVerifier().verify(_Session(rows, anchor_exists=False), "t1", _FROM, _TO)
    assert bad["verified"] is False


async def test_query_failure_raises_never_reports_verified() -> None:
    class _Broken:
        async def execute(self, q: Any, params: Any = None) -> Any:
            if "set_config" in str(q):
                return _Result()
            raise RuntimeError('column "entry_hash" does not exist')

    with pytest.raises(AuditChainVerificationError):
        await HashChainVerifier().verify(_Broken(), "t1", _FROM, _TO)


def test_writer_and_flusher_are_the_real_wal_implementations() -> None:
    v2 = _v2()
    assert audit_v3.AuditWriter is v2.AuditWriter
    assert audit_v3.AuditFlusher is v2.AuditFlusher
