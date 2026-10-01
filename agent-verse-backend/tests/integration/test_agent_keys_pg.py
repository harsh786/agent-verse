"""AGKEY-01/02 on real Postgres (least-privilege role) + real Redis.

An agent key minted on one pod authenticates on another (DB-authoritative under
FORCE RLS via the presented-hash policy), is cached in Redis, and stops working
everywhere the moment it is revoked or its agent is deleted.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.auth.agent_credentials import AgentCredentialStore
from tests._auth_pg import app_role_url, session_factory

pytestmark = pytest.mark.integration


async def _seed(owner_factory: object, tenant_id: str, agent_id: str) -> None:
    async with owner_factory() as s, s.begin():  # type: ignore[operator]
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier) VALUES (:t, 'AK', :e, 'starter')"
            ),
            {"t": tenant_id, "e": f"{tenant_id}@ak.test"},
        )
        await s.execute(
            text(
                "INSERT INTO agents (id, tenant_id, name, autonomy_mode, is_active) "
                "VALUES (:a, :t, 'bot', 'bounded-autonomous', TRUE)"
            ),
            {"a": agent_id, "t": tenant_id},
        )


async def test_agent_key_cross_pod_resolve_cache_and_revoke(pg_url: str, redis_url: str) -> None:
    import redis.asyncio as aioredis

    owner_engine, owner = session_factory(pg_url)
    app_engine, app_factory = session_factory(await app_role_url(pg_url))
    redis = aioredis.from_url(redis_url, decode_responses=True)
    tenant_id, agent_id = uuid.uuid4().hex, uuid.uuid4().hex
    try:
        await _seed(owner, tenant_id, agent_id)
        pod_a, pod_b = AgentCredentialStore(), AgentCredentialStore()
        for pod in (pod_a, pod_b):
            pod.set_db(app_factory)
            pod.set_redis(redis)
        created = await pod_a.create_key_async(
            agent_id=agent_id, tenant_id=tenant_id, name="ci", allowed_tools=["web_*"]
        )
        raw = created["raw_key"]

        ctx = await pod_b.resolve_context(raw)
        assert ctx is not None
        assert ctx.tenant_id == tenant_id and ctx.plan.value == "starter"
        assert ctx.roles == ("agent",) and ctx.agent_key is not None
        assert ctx.agent_key.agent_id == agent_id
        assert ctx.agent_key.tool_denial("web_search") is None
        assert ctx.agent_key.tool_denial("shell") is not None
        keys = await redis.keys("agent_key:*")
        assert keys, "resolution should be cached in the shared Redis"

        # Revoke on pod A: pod B's next request is refused (cache deleted).
        assert await pod_a.revoke_async(created["key_id"], agent_id, tenant_id)
        assert await pod_b.resolve_context(raw) is None

        # A key whose agent was deleted authenticates nothing.
        second = await pod_a.create_key_async(agent_id=agent_id, tenant_id=tenant_id, name="2")
        assert await pod_b.resolve_context(second["raw_key"]) is not None
        await redis.flushdb()
        async with owner() as s, s.begin():
            await s.execute(
                text("UPDATE agents SET is_active = FALSE WHERE id = :a"), {"a": agent_id}
            )
        assert await pod_b.resolve_context(second["raw_key"]) is None
    finally:
        async with owner() as s, s.begin():
            await s.execute(
                text("DELETE FROM agent_api_keys WHERE tenant_id = :t"), {"t": tenant_id}
            )
            await s.execute(text("DELETE FROM agents WHERE tenant_id = :t"), {"t": tenant_id})
            await s.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id})
        await redis.aclose()
        await app_engine.dispose()
        await owner_engine.dispose()


async def test_agent_key_hash_policy_reveals_only_the_presented_row(pg_url: str) -> None:
    owner_engine, owner = session_factory(pg_url)
    app_engine, app_factory = session_factory(await app_role_url(pg_url))
    tenant_id, agent_id = uuid.uuid4().hex, uuid.uuid4().hex
    try:
        await _seed(owner, tenant_id, agent_id)
        store = AgentCredentialStore()
        store.set_db(app_factory)
        await store.create_key_async(agent_id=agent_id, tenant_id=tenant_id, name="a")
        async with app_factory() as s, s.begin():
            visible = (await s.execute(text("SELECT count(*) FROM agent_api_keys"))).scalar()
        assert visible == 0  # no GUC → nothing
    finally:
        async with owner() as s, s.begin():
            await s.execute(
                text("DELETE FROM agent_api_keys WHERE tenant_id = :t"), {"t": tenant_id}
            )
            await s.execute(text("DELETE FROM agents WHERE tenant_id = :t"), {"t": tenant_id})
            await s.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id})
        await app_engine.dispose()
        await owner_engine.dispose()
