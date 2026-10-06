"""Integration (TRG-42): gateway bindings on real Postgres (RLS) + Redis.

Two independent app instances stand in for two replicas: each has its own
``ChannelBindingStore`` (and cache), sharing one Postgres and one Redis. A binding
created through replica A's API routes a signed delivery on replica B; unbinding
on A stops B routing on its next message. Tenant CRUD runs on a NOBYPASSRLS role.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/gateway/test_gateway_bindings_integration.py -m integration
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


class _Chat:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def achannel_turn(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return {"session_id": "s", "reply": "ok", "actions": []}


@pytest_asyncio.fixture
async def env(pg_url: str, redis_url: str) -> AsyncIterator[SimpleNamespace]:
    import redis.asyncio as aioredis

    suffix = secrets.token_hex(4)
    password = secrets.token_urlsafe(24)
    role = f"test_app_gwb_{suffix}"
    admin_engine = create_async_engine(pg_url)
    async with admin_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {role}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON channel_tenant_mappings TO {role}")
        )
        # DEF-3: binding secrets are sealed with the tenant's envelope key.
        await conn.execute(text(f"GRANT SELECT ON tenant_vault_keys TO {role}"))
    app_url = (
        make_url(pg_url).set(username=role, password=password).render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=2, max_overflow=0)
    redis = aioredis.from_url(redis_url)
    yield SimpleNamespace(
        admin=async_sessionmaker(admin_engine, expire_on_commit=False),
        app=async_sessionmaker(app_engine, expire_on_commit=False),
        redis=redis,
        tenant_a=f"ta{suffix}",
        tenant_b=f"tb{suffix}",
    )
    await redis.aclose()
    await app_engine.dispose()
    await admin_engine.dispose()


def _replica(env: SimpleNamespace, current: dict[str, Any]) -> tuple[Any, _Chat]:
    from fastapi import FastAPI, Request

    from app.api.channels.bindings import router as bindings_router
    from app.gateway import router as gw
    from app.gateway.binding_store import ChannelBindingStore
    from app.gateway.channel_registry import ChannelRegistry

    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/channels/bindings"):
            request.state.tenant = current["ctx"]
        return await call_next(request)

    app.include_router(bindings_router)
    app.include_router(gw.router)
    chat = _Chat()
    app.state.chat_service = chat
    app.state.db_session_factory = env.app
    app.state.system_db_session_factory = env.admin
    app.state._rate_limiter_redis = env.redis
    app.state.channel_registry = ChannelRegistry()
    app.state.channel_binding_store = ChannelBindingStore(
        app.state, env_registry=app.state.channel_registry
    )
    return app, chat


def _ctx(tenant_id: str) -> Any:
    from app.tenancy.context import PlanTier, TenantContext

    return TenantContext(
        tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k", roles=("admin",)
    )


def _signed_webhook(secret: str, payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"content-type": "application/json", "x-webhook-signature": sig}


def _client(app: Any) -> Any:
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_binding_created_on_replica_a_routes_on_replica_b(env: SimpleNamespace) -> None:
    current: dict[str, Any] = {"ctx": _ctx(env.tenant_a)}
    app_a, chat_a = _replica(env, current)
    app_b, chat_b = _replica(env, current)
    async with _client(app_a) as a, _client(app_b) as b:
        created = (await a.post("/channels/bindings", json={"channel": "webhook"})).json()
        assert created["status"] == "verified"
        addressee, secret = created["addressee"], created["secret"]
        assert addressee.startswith("wh-") and secret

        # The secret is stored encrypted, never in clear.
        async with env.admin() as session:
            cfg = (
                await session.execute(
                    text("SELECT channel_config FROM channel_tenant_mappings WHERE id = :id"),
                    {"id": created["id"]},
                )
            ).scalar_one()
        assert secret not in json.dumps(cfg)

        payload = {"addressee": addressee, "text": "hello from outside", "user_id": "u1"}
        body, headers = _signed_webhook(secret, payload)
        r = await b.post(f"/v1/gateway/webhook/chat/{addressee}", content=body, headers=headers)
        assert r.status_code == 200, r.text
        assert chat_b.calls and chat_b.calls[0]["tenant_id"] == env.tenant_a

        # Wrong secret on B is refused.
        body2, headers2 = _signed_webhook("not-the-secret", payload)
        bad = await b.post(f"/v1/gateway/webhook/chat/{addressee}", content=body2, headers=headers2)
        assert bad.status_code == 401

        # Tenant B cannot see tenant A's binding (RLS + explicit filter).
        current["ctx"] = _ctx(env.tenant_b)
        assert (await a.get("/channels/bindings")).json() == []
        current["ctx"] = _ctx(env.tenant_a)
        listed = (await a.get("/channels/bindings")).json()
        assert [x["addressee"] for x in listed] == [addressee]
        assert "secret" not in listed[0] and listed[0]["has_secret"] is True

        # Unbind on A: B (which has the binding cached) stops routing at once.
        assert (await a.delete(f"/channels/bindings/{created['id']}")).status_code == 200
        chat_b.calls.clear()
        gone = await b.post(f"/v1/gateway/webhook/chat/{addressee}", content=body, headers=headers)
        assert gone.status_code != 200
        assert chat_b.calls == []
    assert chat_a.calls == []


@pytest.mark.asyncio
async def test_telegram_bot_bound_by_one_tenant_cannot_be_bound_by_another(
    env: SimpleNamespace, monkeypatch: Any
) -> None:
    import app.gateway.binding_verification as bv

    async def _owns(channel: str, addressee: str, *, outbound_token: str, http: Any = None) -> None:
        if outbound_token != f"{addressee}:" + "T" * 30:
            raise bv.BindingOwnershipError("not the bot token")

    monkeypatch.setattr(bv, "verify_ownership", _owns)
    bot = str(10_000_000 + secrets.randbelow(1_000_000))
    token = f"{bot}:" + "T" * 30
    current: dict[str, Any] = {"ctx": _ctx(env.tenant_a)}
    app_a, _ = _replica(env, current)
    async with _client(app_a) as a:
        no_proof = await a.post(
            "/channels/bindings",
            json={"channel": "telegram", "addressee": bot, "outbound_token": "wrong"},
        )
        assert no_proof.status_code == 422
        ok = await a.post(
            "/channels/bindings",
            json={"channel": "telegram", "addressee": bot, "outbound_token": token},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["webhook_url"] == f"/v1/gateway/telegram/chat/{bot}"

        current["ctx"] = _ctx(env.tenant_b)
        taken = await a.post(
            "/channels/bindings",
            json={"channel": "telegram", "addressee": bot, "outbound_token": token},
        )
        assert taken.status_code == 409


@pytest.mark.asyncio
async def test_def3_envelope_sealed_whatsapp_binding_handshake_and_delivery(
    env: SimpleNamespace, monkeypatch: Any
) -> None:
    """DEF-3 on real Postgres: tenant A has its own envelope key, so the binding's
    app secret / access token / verify token are stored ``tv1:`` (unreadable
    with the platform key alone); replica B opens them to answer Meta's
    subscription handshake with the binding's own verify token and to verify an
    X-Hub-Signature-256 delivery; tenant B's verify token is refused."""
    import app.gateway.binding_verification as bv
    from app.providers.tenant_vault import (
        TENANT_CIPHER_PREFIX,
        invalidate_tenant_vault,
        store_tenant_vault_key,
    )

    async def _owns(channel: str, addressee: str, *, outbound_token: str, http: Any = None) -> None:
        return None

    monkeypatch.setattr(bv, "verify_ownership", _owns)
    async with env.admin() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, 'A', :email, 'professional', true) ON CONFLICT DO NOTHING"
            ),
            {"id": env.tenant_a, "email": f"{env.tenant_a}@example.com"},
        )
    await store_tenant_vault_key(env.admin, env.tenant_a, secrets.token_bytes(32))
    invalidate_tenant_vault()
    phone = str(10**9 + secrets.randbelow(10**9))
    app_secret = secrets.token_hex(16)
    current: dict[str, Any] = {"ctx": _ctx(env.tenant_a)}
    app_a, _ = _replica(env, current)
    app_b, chat_b = _replica(env, current)
    async with _client(app_a) as a, _client(app_b) as b:
        created = await a.post(
            "/channels/bindings",
            json={
                "channel": "whatsapp",
                "addressee": phone,
                "secret": app_secret,
                "outbound_token": "EAAG" + secrets.token_hex(8),
            },
        )
        assert created.status_code == 200, created.text
        verify_token = created.json()["verify_token"]
        async with env.admin() as session:
            cfg = (
                await session.execute(
                    text("SELECT channel_config FROM channel_tenant_mappings WHERE id = :id"),
                    {"id": created.json()["id"]},
                )
            ).scalar_one()
        for field in ("secret_enc", "outbound_token_enc", "verify_token_enc"):
            assert cfg[field].startswith(TENANT_CIPHER_PREFIX)
        assert app_secret not in json.dumps(cfg) and verify_token not in json.dumps(cfg)

        hub = {"hub.mode": "subscribe", "hub.verify_token": verify_token, "hub.challenge": "42"}
        ok = await b.get(f"/v1/gateway/whatsapp/chat/{phone}", params=hub)
        assert ok.status_code == 200 and ok.text == "42"
        wrong = await b.get(
            f"/v1/gateway/whatsapp/chat/{phone}", params={**hub, "hub.verify_token": "x"}
        )
        assert wrong.status_code == 403

        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "15550001111", "phone_number_id": phone},
                "contacts": [{"profile": {"name": "Ann"}, "wa_id": "15551234567"}],
                "messages": [{"from": "15551234567", "id": "wamid.X1", "timestamp": "1700000000",
                              "type": "text", "text": {"body": "status please"}}],
            }}]}],
        }
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
        r = await b.post(
            "/v1/gateway/whatsapp/chat",
            content=body,
            headers={"content-type": "application/json", "x-hub-signature-256": sig},
        )
        assert r.status_code == 200, r.text
        assert chat_b.calls and chat_b.calls[0]["tenant_id"] == env.tenant_a
    invalidate_tenant_vault()


@pytest.mark.asyncio
async def test_def3_telegram_binding_registers_its_webhook_with_the_secret_token(
    env: SimpleNamespace, monkeypatch: Any
) -> None:
    import app.gateway.binding_verification as bv

    async def _owns(channel: str, addressee: str, *, outbound_token: str, http: Any = None) -> None:
        return None

    registered: list[tuple[str, str, str]] = []

    async def _set_webhook(token: str, url: str, secret_token: str, *, http: Any = None) -> None:
        registered.append((token, url, secret_token))

    monkeypatch.setattr(bv, "verify_ownership", _owns)
    monkeypatch.setattr(bv, "register_telegram_webhook", _set_webhook)
    monkeypatch.setenv("GATEWAY_PUBLIC_BASE_URL", "https://agents.example.com")
    bot = str(20_000_000 + secrets.randbelow(1_000_000))
    token = f"{bot}:" + "Q" * 30
    current: dict[str, Any] = {"ctx": _ctx(env.tenant_b)}
    app_a, chat_a = _replica(env, current)
    async with _client(app_a) as a:
        created = (
            await a.post(
                "/channels/bindings",
                json={"channel": "telegram", "addressee": bot, "outbound_token": token},
            )
        ).json()
        assert created["webhook_registered"] is True
        url = f"https://agents.example.com/v1/gateway/telegram/chat/{bot}"
        assert created["webhook_url"] == url
        assert registered == [(token, url, created["secret"])]

        update = {"update_id": 1, "message": {"message_id": 7, "text": "hi",
                                              "chat": {"id": 55}, "from": {"id": 55}}}
        r = await a.post(
            f"/v1/gateway/telegram/chat/{bot}",
            json=update,
            headers={"x-telegram-bot-api-secret-token": created["secret"]},
        )
        assert r.status_code == 200, r.text
        assert chat_a.calls and chat_a.calls[0]["tenant_id"] == env.tenant_b
        forged = await a.post(
            f"/v1/gateway/telegram/chat/{bot}",
            json={**update, "update_id": 2},
            headers={"x-telegram-bot-api-secret-token": "nope"},
        )
        assert forged.status_code == 401
