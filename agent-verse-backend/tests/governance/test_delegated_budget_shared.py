"""Recert GRANT item 5: delegated grants share the parent's budget.

``delegate_active_grants`` gave every child the parent's full ``max_cost_usd``
(its ``spent_usd`` ignored), so N spawned children could spend N x the cap. Now:
a child is capped at the parent's *remaining* budget, a child's spend is also
charged to every ancestor, and a delegated grant whose ancestor's budget is
consumed no longer covers anything.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.governance.grants import InMemoryGrantStore, check_grant, delegate_active_grants
from app.governance.grants.models import Grant

NOW = datetime.now(UTC)


def _parent(tenant: str = "t", spent: float = 0.0) -> Grant:
    return Grant(
        grant_id=f"p-{uuid.uuid4().hex[:8]}",
        tenant_id=tenant,
        grantor="admin",
        grantee_agent_id="sup",
        scopes=("read_*",),
        not_before=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
        max_cost_usd=10.0,
        spent_usd=spent,
    )


async def _spawn(store: object, child: str, tenant: str = "t") -> Grant:
    minted = await delegate_active_grants(
        store, tenant_id=tenant, parent_agent_id="sup", child_agent_id=child, now=NOW
    )
    assert len(minted) == 1
    return minted[0]


async def test_child_cap_is_the_parents_remaining_budget() -> None:
    store = InMemoryGrantStore()
    await store.issue(_parent(spent=7.0))
    child = await _spawn(store, "c1")
    assert child.max_cost_usd == pytest.approx(3.0)


async def test_children_share_the_parent_budget() -> None:
    store = InMemoryGrantStore()
    parent = await store.issue(_parent())
    a = await _spawn(store, "c1")
    await _spawn(store, "c2")
    await store.record_spend("t", a.grant_id, 10.0)  # child 1 spends the whole budget
    assert (await store.get("t", parent.grant_id)).spent_usd == pytest.approx(10.0)
    d = await check_grant(store, tenant_id="t", agent_id="c2", tool_name="read_x", now=NOW)
    assert not d.allowed and d.reason == "cost_cap_exceeded"


@pytest.mark.integration
async def test_postgres_spend_charges_ancestors(pg_url: str) -> None:
    from app.governance.grants.postgres_store import PostgresGrantStore
    from tests.memory._pg import app_role_engine, sessionmaker_for

    engine = await app_role_engine(pg_url, ["agent_grants"])
    try:
        store = PostgresGrantStore(sessionmaker_for(engine))
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        parent = await store.issue(_parent(tenant))
        a = await _spawn(store, "c1", tenant)
        await _spawn(store, "c2", tenant)
        await store.record_spend(tenant, a.grant_id, 10.0)
        assert (await store.get(tenant, parent.grant_id)).spent_usd == pytest.approx(10.0)
        d = await check_grant(store, tenant_id=tenant, agent_id="c2", tool_name="read_x", now=NOW)
        assert not d.allowed and d.reason == "cost_cap_exceeded"
    finally:
        await engine.dispose()
