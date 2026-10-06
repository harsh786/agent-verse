"""SAML-01 on real Postgres (least-privilege role, FORCE RLS) + real Redis.

The user-session store JIT-provisions a person, records a session behind a
one-time login code, exchanges it exactly once for a token, resolves the token
on another "pod" (a second store instance) and stops resolving it everywhere
the moment the session is revoked or the membership is deactivated. The full
SAML flow (signed assertion → 303 → exchange → authenticated request) runs
against the same database.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import text

from app.auth.user_sessions import LoginRefusedError, UserSessionStore
from tests._auth_pg import app_role_url, session_factory

pytestmark = pytest.mark.integration


async def _seed_tenant(owner: Any, tenant_id: str) -> None:
    async with owner() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier) VALUES (:t, 'S', :e, 'starter')"
            ),
            {"t": tenant_id, "e": f"{tenant_id}@sess.test"},
        )


async def _cleanup(owner: Any, tenant_id: str, emails: list[str]) -> None:
    async with owner() as s, s.begin():
        await s.execute(text("DELETE FROM user_sessions WHERE tenant_id = :t"), {"t": tenant_id})
        await s.execute(
            text("DELETE FROM tenant_memberships WHERE tenant_id = :t"), {"t": tenant_id}
        )
        await s.execute(text("DELETE FROM saml_configs WHERE tenant_id = :t"), {"t": tenant_id})
        for e in emails:
            await s.execute(text("DELETE FROM users WHERE lower(email) = lower(:e)"), {"e": e})
        await s.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id})


async def test_session_lifecycle_cross_pod(pg_url: str, redis_url: str) -> None:
    import redis.asyncio as aioredis

    owner_engine, owner = session_factory(pg_url)
    app_engine, app_factory = session_factory(await app_role_url(pg_url))
    redis = aioredis.from_url(redis_url, decode_responses=True)
    tenant_id = uuid.uuid4().hex
    email = f"Alice.{tenant_id[:6]}@Corp.test"
    try:
        await _seed_tenant(owner, tenant_id)
        pod_a = UserSessionStore(app_factory, redis)
        pod_b = UserSessionStore(app_factory, redis)

        user_id = await pod_a.provision_member(
            tenant_id=tenant_id, email=email, name="Alice", default_role="operator"
        )
        # Idempotent and case-insensitive on e-mail.
        assert (
            await pod_b.provision_member(tenant_id=tenant_id, email=email.lower(), name=None)
            == user_id
        )
        code = await pod_a.issue_login_code(
            tenant_id=tenant_id, user_id=user_id, auth_method="saml"
        )

        # Exactly one of two concurrent exchanges wins.
        results = await asyncio.gather(pod_a.exchange_code(code), pod_b.exchange_code(code))
        winners = [r for r in results if r is not None]
        assert len(winners) == 1
        token = winners[0]["access_token"]
        assert token.startswith("avs_") and winners[0]["tenant_id"] == tenant_id

        ctx = await pod_b.resolve(token)
        assert ctx is not None and ctx.tenant_id == tenant_id and ctx.user_id == user_id
        assert ctx.roles == ("operator",) and ctx.plan.value == "starter"
        cached = await redis.keys("user_session:*")
        assert cached, "the resolution is cached in the shared Redis"
        assert json.loads(await redis.get(cached[0]))["user_id"] == user_id

        # Logout on pod A: pod B refuses the token at once (cache purged).
        assert await pod_a.revoke_token(token)
        assert await pod_b.resolve(token) is None

        # A deactivated membership (SCIM offboarding) cannot log in again.
        second = await pod_a.exchange_code(
            await pod_a.issue_login_code(tenant_id=tenant_id, user_id=user_id, auth_method="saml")
        )
        assert second is not None
        async with owner() as s, s.begin():
            await s.execute(
                text("UPDATE tenant_memberships SET status = 'deactivated' WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
        await redis.flushdb()
        assert await pod_b.resolve(second["access_token"]) is None
        with pytest.raises(LoginRefusedError):
            await pod_a.provision_member(tenant_id=tenant_id, email=email, name=None)

        # JIT off: a person never provisioned is refused.
        with pytest.raises(LoginRefusedError):
            await pod_a.provision_member(
                tenant_id=tenant_id, email=f"nobody{tenant_id[:4]}@x.test", name=None, jit=False
            )
        # revoke_user_sessions revokes every live session of the person.
        async with owner() as s, s.begin():
            await s.execute(
                text("UPDATE tenant_memberships SET status = 'active' WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
        third = await pod_a.exchange_code(
            await pod_a.issue_login_code(tenant_id=tenant_id, user_id=user_id, auth_method="saml")
        )
        assert third is not None and await pod_b.resolve(third["access_token"]) is not None
        assert await pod_a.revoke_user_sessions(tenant_id, user_id) >= 1
        assert await pod_b.resolve(third["access_token"]) is None
    finally:
        await _cleanup(owner, tenant_id, [email])
        await redis.aclose()
        await app_engine.dispose()
        await owner_engine.dispose()


async def test_saml_login_end_to_end(pg_url: str, redis_url: str) -> None:
    import httpx
    import redis.asyncio as aioredis

    from app.main import create_app
    from tests.auth._saml_idp import IDP_ENTITY, IDP_SSO, make_idp, signed_response

    owner_engine, owner = session_factory(pg_url)
    app_engine, app_factory = session_factory(await app_role_url(pg_url))
    redis = aioredis.from_url(redis_url, decode_responses=True)
    tenant_id = uuid.uuid4().hex
    email = f"saml.{tenant_id[:6]}@corp.test"
    sp = "https://sp.test/saml/metadata"
    try:
        await _seed_tenant(owner, tenant_id)
        async with owner() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO saml_configs (id, tenant_id, idp_entity_id, idp_sso_url, "
                    "idp_cert, sp_entity_id, attribute_mapping, default_role, jit_provisioning, "
                    "is_active, created_at, updated_at) VALUES (:id, :t, :ie, :iu, :c, :sp, "
                    "'{}'::jsonb, 'approver', TRUE, TRUE, NOW(), NOW())"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "t": tenant_id,
                    "ie": IDP_ENTITY,
                    "iu": IDP_SSO,
                    "c": make_idp().cert_b64,
                    "sp": sp,
                },
            )
        app = create_app()
        app.state.db_session_factory = app_factory
        app.state._redis = redis
        app.state.user_session_store.set_db(app_factory)
        app.state.user_session_store.set_redis(redis)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://sp.test") as client:
            resp = signed_response(
                acs_url=f"http://sp.test/enterprise/saml/acs/{tenant_id}",
                sp_entity=sp,
                name_id=email,
            )
            r = await client.post(f"/enterprise/saml/acs/{tenant_id}", data={"SAMLResponse": resp})
            assert r.status_code == 303, r.text
            code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
            r = await client.post("/auth/session/exchange", json={"code": code})
            assert r.status_code == 200, r.text
            assert r.json()["plan"] == "starter"  # the tenant's real plan
            token = r.json()["access_token"]
            r = await client.get("/auth/mfa/status", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200, r.text
            # The code is single use.
            r = await client.post("/auth/session/exchange", json={"code": code})
            assert r.status_code == 401
            # Logout revokes the token in Postgres: it stops authenticating.
            auth = {"Authorization": f"Bearer {token}"}
            r = await client.post("/auth/session/logout", headers=auth)
            assert r.status_code == 204, r.text
            r = await client.get("/auth/mfa/status", headers=auth)
            assert r.status_code == 401, r.text
        async with owner() as s, s.begin():
            revoked_at = (
                await s.execute(
                    text("SELECT revoked_at FROM user_sessions WHERE tenant_id = :t"),
                    {"t": tenant_id},
                )
            ).scalar_one()
        assert revoked_at is not None
        async with owner() as s, s.begin():
            role = (
                await s.execute(
                    text("SELECT role FROM tenant_memberships WHERE tenant_id = :t"),
                    {"t": tenant_id},
                )
            ).scalar_one()
        assert role == "approver"
    finally:
        await _cleanup(owner, tenant_id, [email])
        await redis.aclose()
        await app_engine.dispose()
        await owner_engine.dispose()


async def _live_session(store: UserSessionStore, tenant_id: str, user_id: str) -> str:
    result = await store.exchange_code(
        await store.issue_login_code(tenant_id=tenant_id, user_id=user_id, auth_method="saml")
    )
    assert result is not None
    token = str(result["access_token"])
    assert await store.resolve(token) is not None  # resolved AND cached in Redis
    return token


async def test_scim_deprovisioning_revokes_the_users_sessions(pg_url: str, redis_url: str) -> None:
    """SCIM DELETE and PATCH active=false end every live session at once.

    The resolved context is cached in Redis on another pod; the revocation must
    be in Postgres (so it survives reactivation) AND purge that cache.
    """
    import redis.asyncio as aioredis

    from app.auth.scim_handler import SCIMHandler

    owner_engine, owner = session_factory(pg_url)
    app_engine, app_factory = session_factory(await app_role_url(pg_url))
    redis = aioredis.from_url(redis_url, decode_responses=True)
    tenant_id = uuid.uuid4().hex
    alice = f"alice.{tenant_id[:6]}@corp.test"
    bob = f"bob.{tenant_id[:6]}@corp.test"
    try:
        await _seed_tenant(owner, tenant_id)
        pod_a = UserSessionStore(app_factory, redis)
        pod_b = UserSessionStore(app_factory, redis)
        alice_id = await pod_a.provision_member(tenant_id=tenant_id, email=alice, name=None)
        bob_id = await pod_a.provision_member(tenant_id=tenant_id, email=bob, name=None)
        alice_tokens = [await _live_session(pod_b, tenant_id, alice_id) for _ in range(2)]
        bob_token = await _live_session(pod_b, tenant_id, bob_id)

        scim = SCIMHandler(
            tenant_id=tenant_id,
            config={"allow_user_delete": True},
            db_factory=app_factory,
            session_store=pod_a,
        )
        await scim.delete_user(alice_id)
        for token in alice_tokens:
            assert await pod_b.resolve(token) is None
        assert await pod_b.resolve(bob_token) is not None  # only the deprovisioned user

        await scim.update_user(
            bob,
            {"Operations": [{"op": "replace", "path": "active", "value": False}]},
            partial=True,
        )
        assert await pod_b.resolve(bob_token) is None

        # Reactivation does not resurrect the revoked sessions.
        await scim.update_user(
            alice_id,
            {"Operations": [{"op": "replace", "path": "active", "value": True}]},
            partial=True,
        )
        assert await pod_b.resolve(alice_tokens[0]) is None
        async with owner() as s, s.begin():
            live = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM user_sessions "
                        "WHERE tenant_id = :t AND revoked_at IS NULL"
                    ),
                    {"t": tenant_id},
                )
            ).scalar_one()
        assert live == 0
    finally:
        await _cleanup(owner, tenant_id, [alice, bob])
        await redis.aclose()
        await app_engine.dispose()
        await owner_engine.dispose()


async def _maintenance_factory(pg_url: str) -> tuple[Any, Any]:
    """A BYPASSRLS, non-owner role like MAINTENANCE_DATABASE_URL's."""
    import secrets

    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    password = secrets.token_urlsafe(24)
    role = f"test_us_prune_{secrets.token_hex(4)}"
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:
        quoted = (await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})).scalar()
        await conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD {quoted} BYPASSRLS"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        await conn.execute(text(f"GRANT SELECT, DELETE ON user_sessions TO {role}"))
    await admin.dispose()
    url = make_url(pg_url).set(username=role, password=password)
    engine = create_async_engine(url.render_as_string(hide_password=False))
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def test_prune_deletes_only_sessions_expired_past_the_grace_in_batches(
    pg_url: str,
) -> None:
    """Million-scale: one row per login must not accumulate forever."""
    from app.auth.user_sessions import prune_user_sessions

    owner_engine, owner = session_factory(pg_url)
    tenants = [uuid.uuid4().hex, uuid.uuid4().hex]
    email = f"prune.{tenants[0][:6]}@corp.test"
    try:
        for t in tenants:
            await _seed_tenant(owner, t)
        async with owner() as s, s.begin():
            user_id = (
                await s.execute(
                    text("INSERT INTO users (id, email) VALUES (:i, :e) RETURNING id"),
                    {"i": uuid.uuid4().hex, "e": email},
                )
            ).scalar_one()
            rows = [(f"old{i}", tenants[i % 2], "NOW() - interval '8 days'") for i in range(5)]
            rows += [("recent-expired", tenants[0], "NOW() - interval '1 hour'")]
            rows += [("live", tenants[1], "NOW() + interval '1 hour'")]
            for sid, tid, expires in rows:
                await s.execute(
                    text(
                        "INSERT INTO user_sessions (id, tenant_id, user_id, auth_method, "
                        f"expires_at) VALUES (:id, :t, :u, 'saml', {expires})"
                    ),
                    {"id": sid, "t": tid, "u": user_id},
                )
            indexes = {
                r[0]
                for r in await s.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'user_sessions'")
                )
            }
        mnt, mnt_engine = await _maintenance_factory(pg_url)
        try:
            deleted = await prune_user_sessions(mnt, grace_days=7, batch_size=2)
        finally:
            await mnt_engine.dispose()
        async with owner() as s, s.begin():
            left = sorted(
                r[0]
                for r in await s.execute(
                    text("SELECT id FROM user_sessions WHERE user_id = :u"), {"u": user_id}
                )
            )
        assert deleted == 5
        assert left == ["live", "recent-expired"]
        assert "ix_user_sessions_expires_at" in indexes
    finally:
        for t in tenants:
            await _cleanup(owner, t, [email])
        await owner_engine.dispose()


async def test_self_service_session_management(pg_url: str, redis_url: str) -> None:
    """a10-F240-01: /auth/sessions + /tenants/me/sessions on the real app, real
    Postgres (app role, FORCE RLS) and real Redis. A viewer lists its own live
    sessions, cannot touch another person's, revokes one, then signs out every
    other device; revoked tokens stop authenticating at once on every replica."""
    import httpx
    import redis.asyncio as aioredis

    from app.main import create_app

    owner_engine, owner = session_factory(pg_url)
    app_engine, app_factory = session_factory(await app_role_url(pg_url))
    redis = aioredis.from_url(redis_url, decode_responses=True)
    tenant_id = uuid.uuid4().hex
    other_tenant = uuid.uuid4().hex
    alice = f"alice.{tenant_id[:6]}@corp.test"
    bob = f"bob.{tenant_id[:6]}@corp.test"
    try:
        await _seed_tenant(owner, tenant_id)
        await _seed_tenant(owner, other_tenant)
        store = UserSessionStore(app_factory, redis)
        alice_id = await store.provision_member(tenant_id=tenant_id, email=alice, name="A")
        bob_id = await store.provision_member(tenant_id=tenant_id, email=bob, name="B")
        a1 = await _live_session(store, tenant_id, alice_id)
        a2 = await _live_session(store, tenant_id, alice_id)
        a3 = await _live_session(store, tenant_id, alice_id)
        b1 = await _live_session(store, tenant_id, bob_id)

        app = create_app()
        app.state.db_session_factory = app_factory
        app.state._redis = redis
        app.state.user_session_store.set_db(app_factory)
        app.state.user_session_store.set_redis(redis)

        def h(token: str) -> dict[str, str]:
            return {"Authorization": f"Bearer {token}"}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/auth/sessions", headers=h(a1))
            assert r.status_code == 200, r.text
            mine = r.json()
            assert len(mine) == 3
            assert [s["current"] for s in mine].count(True) == 1
            assert {s["auth_method"] for s in mine} == {"saml"}
            r = await client.get("/tenants/me/sessions", headers=h(b1))
            assert r.status_code == 200, r.text
            bob_sessions = r.json()
            assert len(bob_sessions) == 1
            bob_session_id = bob_sessions[0]["session_id"]

            # Alice cannot revoke Bob's session (404, and it keeps working).
            r = await client.delete(f"/auth/sessions/{bob_session_id}", headers=h(a1))
            assert r.status_code == 404, r.text
            assert (await client.get("/auth/sessions", headers=h(b1))).status_code == 200

            # Revoke one of her own (a viewer may: it is her session).
            current_id = next(s["session_id"] for s in mine if s["current"])
            victim = next(s["session_id"] for s in mine if not s["current"])
            r = await client.delete(f"/tenants/me/sessions/{victim}", headers=h(a1))
            assert r.status_code == 204, r.text
            r = await client.get("/auth/sessions", headers=h(a1))
            assert {s["session_id"] for s in r.json()} == (
                {s["session_id"] for s in mine} - {victim}
            )

            # Sign out every other device: only the current session survives.
            r = await client.delete("/auth/sessions", headers=h(a1))
            assert r.status_code == 200 and r.json() == {"revoked": 1}, r.text
            for token in (a2, a3):
                assert (await client.get("/auth/sessions", headers=h(token))).status_code == 401
            r = await client.get("/auth/sessions", headers=h(a1))
            assert [s["session_id"] for s in r.json()] == [current_id]
            # Bob is untouched.
            assert (await client.get("/auth/sessions", headers=h(b1))).status_code == 200

        # The revocations are in Postgres (and purged from the shared cache):
        # a second store ("another pod") refuses the tokens too.
        pod_b = UserSessionStore(app_factory, redis)
        assert await pod_b.resolve(a2) is None and await pod_b.resolve(a3) is None
        assert await pod_b.resolve(a1) is not None and await pod_b.resolve(b1) is not None
        # Another tenant (its own GUC + predicate) sees none of these sessions.
        assert await pod_b.list_user_sessions(other_tenant, alice_id) == []
    finally:
        await _cleanup(owner, tenant_id, [alice, bob])
        await _cleanup(owner, other_tenant, [])
        await redis.aclose()
        await app_engine.dispose()
        await owner_engine.dispose()
