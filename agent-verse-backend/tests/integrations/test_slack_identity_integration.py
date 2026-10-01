"""Integration (TRG-36): Slack identity links on real Postgres + RLS.

Link codes are issued on the least-privilege NOBYPASSRLS role (the API role);
the superuser engine stands in for the maintenance (BYPASSRLS) factory the
pre-auth Slack routes use. Walks the whole flow: issue a code as an API key,
redeem it with a signed ``/agentverse link`` from the bound workspace, approve a
HITL request from Slack (recorded as that key), then revoke the key.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/integrations/test_slack_identity_integration.py -m integration
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_SECRET = "integration-slack-identity-secret"


@pytest_asyncio.fixture
async def env(pg_url: str) -> AsyncIterator[SimpleNamespace]:
    suffix = secrets.token_hex(4)
    tenant_a, tenant_b = f"ta{suffix}", f"tb{suffix}"
    team_a, team_b = f"TA{suffix}", f"TB{suffix}"
    approver_key, operator_key, b_key = (str(uuid.uuid4()) for _ in range(3))
    password = secrets.token_urlsafe(24)
    role = f"test_app_slackid_{suffix}"
    admin_engine = create_async_engine(pg_url)
    async with admin_engine.begin() as conn:
        for tid in (tenant_a, tenant_b):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :e, 'professional', true)"
                ),
                {"id": tid, "e": f"{tid}@example.test"},
            )
        for kid, tid, roles in (
            (approver_key, tenant_a, '["approver"]'),
            (operator_key, tenant_a, '["operator"]'),
            (b_key, tenant_b, '["admin"]'),
        ):
            await conn.execute(
                text(
                    "INSERT INTO api_keys (id, tenant_id, name, key_hash, roles) "
                    "VALUES (:id, :tid, 'k', :h, CAST(:roles AS jsonb))"
                ),
                {"id": kid, "tid": tid, "h": secrets.token_hex(32), "roles": roles},
            )
        for tid, team in ((tenant_a, team_a), (tenant_b, team_b)):
            await conn.execute(
                text(
                    "INSERT INTO channel_tenant_mappings "
                    "(id, tenant_id, channel_type, channel_id, status) "
                    "VALUES (:id, :tid, 'slack', :team, 'verified')"
                ),
                {"id": str(uuid.uuid4()), "tid": tid, "team": team},
            )
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
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON slack_identity_links TO {role}")
        )
        await conn.execute(text(f"GRANT SELECT ON api_keys TO {role}"))
    app_url = (
        make_url(pg_url).set(username=role, password=password).render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=2, max_overflow=0)
    yield SimpleNamespace(
        admin=async_sessionmaker(admin_engine, expire_on_commit=False),
        app=async_sessionmaker(app_engine, expire_on_commit=False),
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        team_a=team_a,
        team_b=team_b,
        approver_key=approver_key,
        operator_key=operator_key,
        b_key=b_key,
    )
    await app_engine.dispose()
    await admin_engine.dispose()


def _build_app(env: SimpleNamespace, current: dict[str, Any]) -> tuple[Any, MagicMock]:
    from fastapi import FastAPI, Request

    from app.api.channels.identities import router as identities_router
    from app.api.integrations import router as integrations_router

    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/channels/identities"):
            request.state.tenant = current["ctx"]
        return await call_next(request)

    app.include_router(identities_router)
    app.include_router(integrations_router)
    hitl = MagicMock()
    hitl.approve_async = AsyncMock(return_value=True)
    hitl.reject = AsyncMock()
    app.state.hitl_gateway = hitl
    app.state.settings = SimpleNamespace()
    app.state.db_session_factory = env.app
    app.state.system_db_session_factory = env.admin
    return app, hitl


def _ctx(tenant_id: str, key: str, roles: tuple[str, ...]) -> Any:
    from app.tenancy.context import PlanTier, TenantContext

    return TenantContext(
        tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id=key, roles=roles
    )


def _signed(body: bytes, content_type: str) -> dict[str, str]:
    ts = str(int(time.time()))
    sig = (
        "v0="
        + hmac.new(
            _SECRET.encode(), f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256
        ).hexdigest()
    )
    return {"Content-Type": content_type, "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


async def _slash(client: Any, team: str, user: str, text_: str) -> dict[str, Any]:
    body = urllib.parse.urlencode({"text": text_, "user_id": user, "team_id": team}).encode()
    resp = await client.post(
        "/integrations/slack/commands",
        content=body,
        headers=_signed(body, "application/x-www-form-urlencoded"),
    )
    assert resp.status_code == 200
    return dict(resp.json())


async def _click(client: Any, team: str, user: str) -> dict[str, Any]:
    body = json.dumps(
        {
            "type": "block_actions",
            "team": {"id": team},
            "user": {"id": user},
            "actions": [{"action_id": "approve_hitl", "value": "req-42"}],
        }
    ).encode()
    resp = await client.post(
        "/integrations/slack/events", content=body, headers=_signed(body, "application/json")
    )
    assert resp.status_code == 200
    return dict(resp.json())


@pytest.mark.asyncio
async def test_link_approve_revoke_flow(env: SimpleNamespace, monkeypatch: Any) -> None:
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SECRET)
    current: dict[str, Any] = {"ctx": _ctx(env.tenant_a, env.approver_key, ("approver",))}
    app, hitl = _build_app(env, current)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        # Unlinked: the click decides nothing.
        refused = await _click(client, env.team_a, "U-ALICE")
        assert "not linked" in refused["text"]
        hitl.approve_async.assert_not_awaited()

        issued = (await client.post("/channels/identities/link-codes", json={})).json()
        assert issued["status"] == "pending" and issued["code"]

        # A code issued in tenant A cannot be redeemed from tenant B's workspace.
        foreign = await _slash(client, env.team_b, "U-ALICE", f"link {issued['code']}")
        assert "invalid or expired" in foreign["text"]

        linked = await _slash(client, env.team_a, "U-ALICE", f"link {issued['code']}")
        assert "now linked" in linked["text"]
        # One-time: the same code does not link a second Slack user.
        again = await _slash(client, env.team_a, "U-MALLORY", f"link {issued['code']}")
        assert "invalid or expired" in again["text"]

        await _click(client, env.team_a, "U-ALICE")
        hitl.approve_async.assert_awaited_once()
        assert hitl.approve_async.await_args.kwargs["approver"] == env.approver_key

        # The link is visible to its owner, not to another tenant (RLS + filter).
        mine = (await client.get("/channels/identities")).json()
        assert [r["slack_user_id"] for r in mine] == ["U-ALICE"]
        current["ctx"] = _ctx(env.tenant_b, env.b_key, ("admin",))
        assert (await client.get("/channels/identities")).json() == []
        current["ctx"] = _ctx(env.tenant_a, env.approver_key, ("approver",))

        # Revoking the API key revokes its Slack authority immediately.
        async with env.admin() as session, session.begin():
            await session.execute(
                text("UPDATE api_keys SET is_active = false WHERE id = :id"),
                {"id": env.approver_key},
            )
        hitl.approve_async.reset_mock()
        revoked = await _click(client, env.team_a, "U-ALICE")
        assert revoked["response_type"] == "ephemeral"
        hitl.approve_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_operator_link_cannot_approve(env: SimpleNamespace, monkeypatch: Any) -> None:
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SECRET)
    current: dict[str, Any] = {"ctx": _ctx(env.tenant_a, env.operator_key, ("operator",))}
    app, hitl = _build_app(env, current)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        code = (await client.post("/channels/identities/link-codes", json={})).json()["code"]
        assert "now linked" in (await _slash(client, env.team_a, "U-OP", f"link {code}"))["text"]
        refused = await _click(client, env.team_a, "U-OP")
        assert "governance:approve" in refused["text"]
        hitl.approve_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_ephemeral_identity_cannot_issue_a_code(env: SimpleNamespace) -> None:
    from httpx import ASGITransport, AsyncClient

    current: dict[str, Any] = {"ctx": _ctx(env.tenant_a, "sso:not-a-key", ("admin",))}
    app, _ = _build_app(env, current)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post("/channels/identities/link-codes", json={})
    assert resp.status_code == 409
