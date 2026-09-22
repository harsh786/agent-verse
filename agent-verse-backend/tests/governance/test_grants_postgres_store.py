"""PostgresGrantStore (app.governance.grants.postgres_store) — Postgres-backed
grant persistence with the same interface as InMemoryGrantStore. A real
Postgres instance is unavailable in this environment, so the SQLAlchemy
AsyncSession is mocked to exercise the row (de)serialization and query
composition logic directly.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.governance.grants.models import Grant
from app.governance.grants.postgres_store import PostgresGrantStore


def _grant(**overrides):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    defaults = dict(
        grant_id="g1",
        tenant_id="t1",
        grantor="user-1",
        grantee_agent_id="agent-1",
        scopes=("tool.*",),
        not_before=now,
        expires_at=now + timedelta(days=1),
        max_cost_usd=10.0,
        revoked=False,
        parent_grant_id=None,
        metadata={"reason": "test"},
    )
    defaults.update(overrides)
    return Grant(**defaults)


def _row_dict(grant: Grant, *, scopes_as_str=False, meta_as_str=False):
    row = {
        "grant_id": grant.grant_id,
        "tenant_id": grant.tenant_id,
        "grantor": grant.grantor,
        "grantee_agent_id": grant.grantee_agent_id,
        "scopes": json.dumps(list(grant.scopes)) if scopes_as_str else list(grant.scopes),
        "not_before": grant.not_before,
        "expires_at": grant.expires_at,
        "max_cost_usd": grant.max_cost_usd,
        "revoked": grant.revoked,
        "parent_grant_id": grant.parent_grant_id,
        "metadata": json.dumps(grant.metadata) if meta_as_str else grant.metadata,
    }
    return row


class _Result:
    def __init__(self, mapping_one=None, mapping_all=None):
        self._mapping_one = mapping_one
        self._mapping_all = mapping_all if mapping_all is not None else []

    def mappings(self):
        return self

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


# ── issue ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_issue_inserts_then_reads_back():
    grant = _grant()
    calls = []

    async def fake_execute(query, params=None):
        sql = str(query)
        calls.append(sql)
        if "INSERT INTO agent_grants" in sql:
            assert params["grant_id"] == "g1"
            assert json.loads(params["scopes"]) == ["tool.*"]
            return _Result()
        if "SELECT" in sql:
            return _Result(mapping_one=_row_dict(grant))
        return _Result()

    store = PostgresGrantStore(_fake_session_factory(fake_execute))
    result = await store.issue(grant)

    assert result.grant_id == "g1"
    assert result.scopes == ("tool.*",)
    assert any("INSERT INTO agent_grants" in c for c in calls)


@pytest.mark.asyncio
async def test_issue_falls_back_to_input_grant_if_read_back_misses():
    grant = _grant(grant_id="g-missing")

    async def fake_execute(query, params=None):
        sql = str(query)
        if "INSERT INTO agent_grants" in sql:
            return _Result()
        return _Result(mapping_one=None)

    store = PostgresGrantStore(_fake_session_factory(fake_execute))
    result = await store.issue(grant)
    assert result is grant


# ── get ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_none_when_missing():
    async def fake_execute(query, params=None):
        return _Result(mapping_one=None)

    store = PostgresGrantStore(_fake_session_factory(fake_execute))
    assert await store.get("t1", "no-such-grant") is None


@pytest.mark.asyncio
async def test_get_deserializes_json_string_columns():
    grant = _grant(scopes=("a.*", "b.*"), metadata={"k": "v"})

    async def fake_execute(query, params=None):
        return _Result(mapping_one=_row_dict(grant, scopes_as_str=True, meta_as_str=True))

    store = PostgresGrantStore(_fake_session_factory(fake_execute))
    result = await store.get("t1", grant.grant_id)
    assert result is not None
    assert result.scopes == ("a.*", "b.*")
    assert result.metadata == {"k": "v"}


# ── revoke ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_updates_and_returns_revoked_grant():
    grant = _grant(revoked=True)
    update_called = []

    async def fake_execute(query, params=None):
        sql = str(query)
        if "UPDATE agent_grants SET revoked" in sql:
            update_called.append(params)
            return _Result()
        return _Result(mapping_one=_row_dict(grant))

    store = PostgresGrantStore(_fake_session_factory(fake_execute))
    result = await store.revoke("t1", grant.grant_id)
    assert result is not None
    assert result.revoked is True
    assert update_called[0] == {"gid": grant.grant_id}


# ── list_for_agent / active_for_agent ───────────────────────────────────


@pytest.mark.asyncio
async def test_list_for_agent_returns_all_rows_as_grants():
    g1 = _grant(grant_id="g1")
    g2 = _grant(grant_id="g2", revoked=True)

    async def fake_execute(query, params=None):
        return _Result(mapping_all=[_row_dict(g1), _row_dict(g2)])

    store = PostgresGrantStore(_fake_session_factory(fake_execute))
    grants = await store.list_for_agent("t1", "agent-1")
    assert {g.grant_id for g in grants} == {"g1", "g2"}


@pytest.mark.asyncio
async def test_active_for_agent_filters_out_revoked_and_expired():
    now = datetime(2026, 1, 2, tzinfo=UTC)
    active = _grant(
        grant_id="active",
        not_before=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=1),
        revoked=False,
    )
    revoked = _grant(grant_id="revoked", revoked=True)
    expired = _grant(
        grant_id="expired",
        not_before=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
        revoked=False,
    )

    async def fake_execute(query, params=None):
        return _Result(mapping_all=[_row_dict(active), _row_dict(revoked), _row_dict(expired)])

    store = PostgresGrantStore(_fake_session_factory(fake_execute))
    grants = await store.active_for_agent("t1", "agent-1", now=now)
    assert [g.grant_id for g in grants] == ["active"]
