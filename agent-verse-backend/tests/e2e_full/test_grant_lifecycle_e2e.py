"""e2e_full: Grantex against real Postgres — persistence, isolation, cascade, budget.

The in-memory grant store is a genuine implementation, so every unit test of the
grant model passes without ever touching a database. These exercise the store the
lifespan actually installs (``PostgresGrantStore``), which is the one that has to
be right for a multi-replica deployment: a grant revoked on one replica must be
revoked everywhere, and cumulative spend must survive a process restart.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


def _store(app: Any) -> Any:
    from app.governance.grants.postgres_store import PostgresGrantStore

    store = getattr(app.state, "grant_store", None)
    assert store is not None, "no grant store wired"
    assert isinstance(store, PostgresGrantStore), (
        f"lifespan left an in-memory grant store ({type(store).__name__}) — grants "
        "would not survive a restart and would diverge across replicas"
    )
    return store


def _grant(tenant_id: str, **over: Any) -> Any:
    from app.governance.grants import Grant

    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "grant_id": uuid.uuid4().hex,
        "tenant_id": tenant_id,
        "grantor": "admin",
        "grantee_agent_id": "agent-root",
        "scopes": ("jira.*",),
        "not_before": now - timedelta(minutes=5),
        "expires_at": now + timedelta(hours=2),
    }
    base.update(over)
    return Grant(**base)


async def test_grants_persist_and_are_tenant_isolated(
    app: Any, tenant_client: Any
) -> None:
    store = _store(app)
    me = (await tenant_client.get("/tenants/me")).json()
    tenant_a = str(me["tenant_id"])
    tenant_b = f"tenant-{uuid.uuid4().hex[:12]}"

    issued = await store.issue(_grant(tenant_a))
    read_back = await store.get(tenant_a, issued.grant_id)
    assert read_back is not None, "grant did not reach Postgres"
    assert read_back.scopes == ("jira.*",)

    assert await store.get(tenant_b, issued.grant_id) is None, (
        "a grant was readable from another tenant's context"
    )
    assert await store.list_for_agent(tenant_b, "agent-root") == ()


async def test_revocation_cascades_through_the_delegation_chain(
    app: Any, tenant_client: Any
) -> None:
    """The guarantee under incident response: revoke once, the whole chain dies."""
    from app.governance.grants import check_grant
    from app.governance.grants.delegation import mint_delegation

    store = _store(app)
    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])
    now = datetime.now(UTC)

    root = await store.issue(_grant(tenant_id, grantee_agent_id="agent-root"))
    child = await store.issue(
        mint_delegation(
            root,
            grant_id=uuid.uuid4().hex,
            grantee_agent_id="agent-child",
            scopes=("jira.search",),
            expires_at=root.expires_at,
            now=now,
        )
    )
    await store.issue(
        mint_delegation(
            child,
            grant_id=uuid.uuid4().hex,
            grantee_agent_id="agent-grandchild",
            scopes=("jira.search",),
            expires_at=child.expires_at,
            now=now,
        )
    )

    for agent in ("agent-root", "agent-child", "agent-grandchild"):
        d = await check_grant(
            store, tenant_id=tenant_id, agent_id=agent, tool_name="jira.search", now=now
        )
        assert d.allowed is True, f"{agent} should hold authority before revocation"

    await store.revoke(tenant_id, root.grant_id)

    for agent in ("agent-root", "agent-child", "agent-grandchild"):
        d = await check_grant(
            store, tenant_id=tenant_id, agent_id=agent, tool_name="jira.search", now=now
        )
        assert d.allowed is False, (
            f"{agent} still holds authority after the root grant was revoked"
        )


async def test_spend_accumulates_durably_and_closes_the_budget(
    app: Any, tenant_client: Any
) -> None:
    """Spend is an atomic SQL increment, so concurrent replicas cannot lose charges."""
    import asyncio

    from app.governance.grants import check_grant

    store = _store(app)
    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])
    now = datetime.now(UTC)

    grant = await store.issue(
        _grant(tenant_id, grantee_agent_id="agent-spender", max_cost_usd=10.0)
    )

    # Ten concurrent $1 charges, as ten executors on ten replicas would issue
    # them. A read-modify-write would lose some; the SQL increment cannot.
    await asyncio.gather(
        *(store.record_spend(tenant_id, grant.grant_id, 1.0) for _ in range(10))
    )

    refreshed = await store.get(tenant_id, grant.grant_id)
    assert refreshed is not None
    assert refreshed.spent_usd == pytest.approx(10.0), (
        f"concurrent charges were lost: spent_usd={refreshed.spent_usd}"
    )

    decision = await check_grant(
        store,
        tenant_id=tenant_id,
        agent_id="agent-spender",
        tool_name="jira.search",
        now=now,
    )
    assert decision.allowed is False
    assert decision.reason == "cost_cap_exceeded"
