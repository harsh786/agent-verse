"""a03-F055-02: PostgresGrantStore.list_children under the app role (RLS + tenant predicate)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.governance.grants import Grant
from app.governance.grants.delegation import mint_delegation
from app.governance.grants.postgres_store import PostgresGrantStore
from tests.memory._pg import app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_NOW = datetime.now(UTC)


def _root(tenant: str, gid: str) -> Grant:
    return Grant(
        grant_id=gid,
        tenant_id=tenant,
        grantor="user:alice",
        grantee_agent_id="parent",
        scopes=("jira.*",),
        not_before=_NOW - timedelta(minutes=5),
        expires_at=_NOW + timedelta(hours=1),
        max_cost_usd=5.0,
    )


async def test_list_children_is_tenant_scoped(pg_url: str) -> None:
    engine = await app_role_engine(pg_url, ["agent_grants"])
    try:
        store = PostgresGrantStore(sessionmaker_for(engine))
        root = await store.issue(_root("t-a", "root-a"))
        child = mint_delegation(
            root,
            grant_id="child-a",
            grantee_agent_id="kid",
            scopes=("jira.search",),
            expires_at=root.expires_at,
            max_cost_usd=1.0,
            now=_NOW,
        )
        await store.issue(child)
        # Another tenant's grant claiming the same parent id is never listed.
        other = Grant(**{**_root("t-b", "child-b").__dict__, "parent_grant_id": "root-a"})
        await store.issue(other)

        kids = await store.list_children("t-a", "root-a")
        assert [g.grant_id for g in kids] == ["child-a"]
        assert kids[0].parent_grant_id == "root-a"
        assert await store.list_children("t-a", "child-a") == ()
    finally:
        await engine.dispose()
