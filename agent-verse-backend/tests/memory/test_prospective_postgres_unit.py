"""PostgresProspectiveMemoryService (app.memory.prospective_postgres) — unit
tests against a mocked SQLAlchemy AsyncSession. A real Postgres instance is
unavailable in this environment (see tests/memory/test_prospective_postgres.py
for the full lifecycle integration test, which is skipped without Docker);
these tests exercise the row mapping, leasing state machine, and
authorization/fencing-token error paths directly.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.memory.prospective import ProspectiveMemory
from app.memory.prospective_postgres import PostgresProspectiveMemoryService

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _item(**overrides):
    defaults = dict(
        memory_id="m1",
        tenant_id="t1",
        intention="call vendor",
        due_at=_NOW - timedelta(hours=1),
        expires_at=_NOW + timedelta(days=1),
        state="pending",
        source_goal_id="g1",
        source_execution_id="e1",
        policy_snapshot={},
        classification="internal",
        idempotency_key="key1",
    )
    defaults.update(overrides)
    return ProspectiveMemory(**defaults)


def _row(item: ProspectiveMemory, **overrides):
    row = {
        "memory_id": item.memory_id,
        "tenant_id": item.tenant_id,
        "intention": item.intention,
        "due_at": item.due_at,
        "expires_at": item.expires_at,
        "state": item.state,
        "source_goal_id": item.source_goal_id,
        "source_execution_id": item.source_execution_id,
        "policy_snapshot": item.policy_snapshot,
        "classification": item.classification,
        "idempotency_key": item.idempotency_key,
        "attempts": item.attempts,
        "fencing_token": item.fencing_token,
        "lease_expires_at": item.lease_expires_at,
        "result": item.result,
    }
    row.update(overrides)
    return row


class _Result:
    def __init__(self, mapping_one=None, mapping_all=None):
        self._mapping_one = mapping_one
        self._mapping_all = mapping_all if mapping_all is not None else []

    def mappings(self):
        return self

    def one(self):
        return self._mapping_one

    def one_or_none(self):
        return self._mapping_one

    def all(self):
        return self._mapping_all


def _fake_session_factory(execute_side_effect):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=session)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)
    session.execute = AsyncMock(side_effect=execute_side_effect)

    def factory():
        return session

    return factory


# ── create ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_inserts_then_reads_back_by_idempotency_key():
    item = _item()
    calls = []

    async def fake_execute(query, params=None):
        sql = str(query)
        calls.append(sql)
        if "INSERT INTO prospective_memory" in sql:
            assert params["idempotency_key"] == "key1"
            return _Result()
        return _Result(mapping_one=_row(item))

    svc = PostgresProspectiveMemoryService(_fake_session_factory(fake_execute))
    result = await svc.create(item)
    assert result.memory_id == "m1"
    assert any("INSERT INTO prospective_memory" in c for c in calls)


# ── get ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_none_when_missing():
    async def fake_execute(query, params=None):
        return _Result(mapping_one=None)

    svc = PostgresProspectiveMemoryService(_fake_session_factory(fake_execute))
    assert await svc.get("t1", "no-such-id") is None


@pytest.mark.asyncio
async def test_get_deserializes_json_string_fields():
    item = _item(policy_snapshot={"a": 1})
    import json

    async def fake_execute(query, params=None):
        return _Result(
            mapping_one=_row(
                item,
                policy_snapshot=json.dumps({"a": 1}),
                result=json.dumps({"ok": True}),
            )
        )

    svc = PostgresProspectiveMemoryService(_fake_session_factory(fake_execute))
    result = await svc.get("t1", "m1")
    assert result is not None
    assert result.policy_snapshot == {"a": 1}
    assert result.result == {"ok": True}


# ── list_active ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_active_maps_rows_in_order():
    a = _item(memory_id="a", idempotency_key="a")
    b = _item(memory_id="b", idempotency_key="b", due_at=_NOW + timedelta(hours=2))

    async def fake_execute(query, params=None):
        return _Result(mapping_all=[_row(a), _row(b)])

    svc = PostgresProspectiveMemoryService(_fake_session_factory(fake_execute))
    result = await svc.list_active("t1", now=_NOW)
    assert [r.memory_id for r in result] == ["a", "b"]


# ── lease_due ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lease_due_expires_overdue_then_claims_due_rows():
    item = _item(state="leased", fencing_token=1)
    calls = []

    async def fake_execute(query, params=None):
        sql = str(query)
        calls.append(sql)
        if "state='expired'" in sql:
            return _Result()
        if "state='leased'" in sql and "RETURNING" in sql:
            return _Result(mapping_all=[_row(item, state="leased", fencing_token=2)])
        return _Result()

    svc = PostgresProspectiveMemoryService(_fake_session_factory(fake_execute))
    leased = await svc.lease_due("t1", now=_NOW, lease_duration=timedelta(minutes=5))
    assert len(leased) == 1
    assert leased[0].fencing_token == 2
    assert any("state='expired'" in c for c in calls)


# ── complete ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_complete_unauthorized_raises_without_touching_db():
    async def fake_execute(query, params=None):
        raise AssertionError("must not query DB when unauthorized")

    svc = PostgresProspectiveMemoryService(_fake_session_factory(fake_execute))
    with pytest.raises(PermissionError):
        await svc.complete("t1", "m1", fencing_token=1, authorized=False, result={})


@pytest.mark.asyncio
async def test_complete_success_returns_completed_item():
    item = _item(state="completed")

    async def fake_execute(query, params=None):
        return _Result(mapping_one=_row(item, result={"ok": True}))

    svc = PostgresProspectiveMemoryService(_fake_session_factory(fake_execute))
    result = await svc.complete("t1", "m1", fencing_token=1, authorized=True, result={"ok": True})
    assert result.state == "completed"


@pytest.mark.asyncio
async def test_complete_stale_fencing_token_raises_runtime_error():
    async def fake_execute(query, params=None):
        return _Result(mapping_one=None)

    svc = PostgresProspectiveMemoryService(_fake_session_factory(fake_execute))
    with pytest.raises(RuntimeError):
        await svc.complete("t1", "m1", fencing_token=999, authorized=True, result={})


# ── cancel ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_success_returns_cancelled_item():
    item = _item(state="cancelled")

    async def fake_execute(query, params=None):
        return _Result(mapping_one=_row(item, result={"cancellation_reason": "no longer needed"}))

    svc = PostgresProspectiveMemoryService(_fake_session_factory(fake_execute))
    result = await svc.cancel("t1", "m1", reason="no longer needed")
    assert result.state == "cancelled"


@pytest.mark.asyncio
async def test_cancel_terminal_item_raises_runtime_error():
    async def fake_execute(query, params=None):
        return _Result(mapping_one=None)

    svc = PostgresProspectiveMemoryService(_fake_session_factory(fake_execute))
    with pytest.raises(RuntimeError):
        await svc.cancel("t1", "m1", reason="too late")
