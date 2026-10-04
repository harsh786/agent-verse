"""GRANT-08: the per-tool-call grant lookup reads only usable grants, bounded.

``enforce_tool_call`` called ``list_for_agent`` on every tool call, and the
Postgres store selected every grant ever issued to the agent (revoked and
expired included, no LIMIT); delegation mints a fresh grant per spawn, so this
grew without bound for a long-lived agent. The hot path now asks the store for
the agent's active grants (filtered and limited in SQL); the full list is only
for the API.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
import pytest

from app.governance.grants import InMemoryGrantStore, check_grant
from app.governance.grants.models import Grant

NOW = datetime.now(UTC)


def _grant(agent: str, *, revoked: bool = False, expired: bool = False, tenant: str = "t") -> Grant:
    return Grant(
        grant_id=uuid.uuid4().hex,
        tenant_id=tenant,
        grantor="admin",
        grantee_agent_id=agent,
        scopes=("read_*",),
        not_before=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1) if expired else NOW + timedelta(hours=1),
        revoked=revoked,
    )


class _NoFullListStore(InMemoryGrantStore):
    async def list_for_agent(self, tenant_id: str, agent_id: str) -> tuple[Grant, ...]:
        raise AssertionError("the hot path must not load every grant ever issued")


async def test_check_grant_uses_active_lookup_not_full_list() -> None:
    store = _NoFullListStore()
    await store.issue(_grant("a1"))
    d = await check_grant(store, tenant_id="t", agent_id="a1", tool_name="read_file", now=NOW)
    assert d.allowed


async def test_denial_reasons_survive() -> None:
    store = _NoFullListStore()
    d = await check_grant(store, tenant_id="t", agent_id="a1", tool_name="read_file", now=NOW)
    assert d.reason == "no_grant_for_agent"
    await store.issue(_grant("a1", revoked=True))
    d = await check_grant(store, tenant_id="t", agent_id="a1", tool_name="read_file", now=NOW)
    assert d.reason == "all_grants_expired_or_revoked"


@pytest.mark.integration
async def test_postgres_active_lookup_filters_in_sql(pg_url: str) -> None:
    from app.governance.grants.postgres_store import PostgresGrantStore
    from tests.memory._pg import app_role_engine, sessionmaker_for

    engine = await app_role_engine(pg_url, ["agent_grants"])
    try:
        store = PostgresGrantStore(sessionmaker_for(engine))
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        live = await store.issue(_grant("a1", tenant=tenant))
        await store.issue(_grant("a1", tenant=tenant, revoked=True))
        await store.issue(_grant("a1", tenant=tenant, expired=True))
        await store.issue(_grant("a2", tenant=tenant))
        active = await store.active_for_agent(tenant, "a1", now=NOW)
        assert [g.grant_id for g in active] == [live.grant_id]
        assert await store.has_any_for_agent(tenant, "a1")
        assert not await store.has_any_for_agent(tenant, "nobody")
        d = await check_grant(
            store, tenant_id=tenant, agent_id="a1", tool_name="read_file", now=NOW
        )
        assert d.allowed and d.grant_id == live.grant_id
    finally:
        await engine.dispose()
