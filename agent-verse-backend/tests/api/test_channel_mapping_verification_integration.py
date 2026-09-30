"""Integration (TRG-03): channel ownership proof against real Postgres + RLS.

The tenant CRUD runs on a least-privilege NOBYPASSRLS role (the production API
role); the superuser engine stands in for the maintenance (BYPASSRLS) factory
used by the pre-auth inbound lookup. A mapping that existed before the migration
is seeded at the previous head so the legacy backfill is exercised for real.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_channel_mapping_verification_integration.py -m integration
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PREVIOUS_HEAD = "61e5532b6fb3"
_SLACK_SECRET = "integration-slack-secret"
_LEGACY_TENANT = "legacy-tenant"
_LEGACY_TEAM = "TLEGACY01"
_OTHER_TENANTS = ("tenant-one", "tenant-two")
_OWNERSHIP_PROOF_HEAD = "d4e9a1c7b3f2"
_PENDING_SMS = "+15550100"
_LEGACY_EMAIL = "legacy@example.test"
_ADMIN_KEY = "integration-admin-key-" + "x" * 16


def _alembic(url: str, *args: str) -> None:
    subprocess.run(
        ["alembic", *args],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
        text=True,
    )


async def _seed_pre_migration(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            for tid in (_LEGACY_TENANT, *_OTHER_TENANTS):
                await conn.execute(
                    text(
                        "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                        "VALUES (:id, :id, :e, 'free', true)"
                    ),
                    {"id": tid, "e": f"{tid}@example.test"},
                )
            await conn.execute(
                text(
                    "INSERT INTO channel_tenant_mappings "
                    "(id, tenant_id, channel_type, channel_id) "
                    "VALUES ('legacy-1', :t, 'slack', :c)"
                ),
                {"t": _LEGACY_TENANT, "c": _LEGACY_TEAM},
            )
    finally:
        await engine.dispose()


async def _seed_before_operator_approval(url: str) -> None:
    """At the TRG-03 revision: an SMS claim still pending a code (it must move
    to the operator queue) and a legacy email mapping (keeps routing)."""
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO channel_tenant_mappings "
                    "(id, tenant_id, channel_type, channel_id, status, "
                    "verification_code_hash, verification_expires_at) "
                    "VALUES ('pending-sms-1', :t, 'sms', :c, 'pending_verification', "
                    "'deadbeef', now() + interval '1 day')"
                ),
                {"t": _OTHER_TENANTS[0], "c": _PENDING_SMS},
            )
            await conn.execute(
                text(
                    "INSERT INTO channel_tenant_mappings "
                    "(id, tenant_id, channel_type, channel_id, status) "
                    "VALUES ('legacy-email-1', :t, 'email', :c, 'legacy_unverified')"
                ),
                {"t": _LEGACY_TENANT, "c": _LEGACY_EMAIL},
            )
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        _alembic(url, "upgrade", _PREVIOUS_HEAD)
        asyncio.run(_seed_pre_migration(url))
        _alembic(url, "upgrade", _OWNERSHIP_PROOF_HEAD)
        asyncio.run(_seed_before_operator_approval(url))
        _alembic(url, "upgrade", "head")
        yield url


@pytest_asyncio.fixture(scope="function")
async def dbs(postgres_url: str) -> AsyncIterator[SimpleNamespace]:
    password = secrets.token_urlsafe(24)
    role = f"test_app_chan_{secrets.token_hex(4)}"
    admin_engine = create_async_engine(postgres_url)
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
    app_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0)
    yield SimpleNamespace(
        admin=async_sessionmaker(admin_engine, expire_on_commit=False),
        app=async_sessionmaker(app_engine, expire_on_commit=False),
    )
    await app_engine.dispose()
    await admin_engine.dispose()


def _build_app(dbs: SimpleNamespace, current: dict[str, str]) -> tuple[Any, AsyncMock]:
    from fastapi import FastAPI, Request

    from app.api.channels.ingestion import router as channels_router

    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        if not request.url.path.endswith("/events"):
            request.state.tenant = SimpleNamespace(tenant_id=current["tid"], plan="free")
        return await call_next(request)

    from app.api.admin import router as admin_router

    app.include_router(channels_router)
    app.include_router(admin_router)
    gateway = AsyncMock()
    app.state.channel_gateway = gateway
    app.state.trigger_event_redis = None
    app.state.slack_signing_secret = _SLACK_SECRET
    app.state.db_session_factory = dbs.app
    app.state.system_db_session_factory = dbs.admin
    return app, gateway


def _client(app: Any) -> Any:
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _slack_message(client: Any, team: str, text_: str) -> Any:
    body = json.dumps(
        {"type": "event_callback", "team_id": team, "event": {"type": "message", "text": text_}}
    ).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(
        _SLACK_SECRET.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256
    ).hexdigest()
    return await client.post(
        "/channels/slack/events",
        content=body,
        headers={
            "X-Slack-Signature": sig,
            "X-Slack-Request-Timestamp": ts,
            "Content-Type": "application/json",
        },
    )


async def _resolve(dbs: SimpleNamespace, team: str) -> str | None:
    from app.api.channels.ingestion import _resolve_tenant_from_channel

    return await _resolve_tenant_from_channel("slack", team, dbs.admin)


@pytest.mark.asyncio
async def test_existing_mapping_is_migrated_to_legacy_and_keeps_routing(
    dbs: SimpleNamespace,
) -> None:
    current = {"tid": _LEGACY_TENANT}
    app, gateway = _build_app(dbs, current)
    async with _client(app) as client:
        assert await _resolve(dbs, _LEGACY_TEAM) == _LEGACY_TENANT
        listed = (await client.get("/channels/mappings?status=legacy_unverified")).json()
        assert [(m["channel_id"], m["status"]) for m in listed] == [
            (_LEGACY_TEAM, "legacy_unverified"),
            (_LEGACY_EMAIL, "legacy_unverified"),
        ]
        resp = await _slack_message(client, _LEGACY_TEAM, "hello")
        assert resp.status_code == 200
        assert gateway.ingest.await_args.kwargs["tenant_id"] == _LEGACY_TENANT


@pytest.mark.asyncio
async def test_pending_mapping_routes_nothing_until_the_code_arrives_on_the_channel(
    dbs: SimpleNamespace,
) -> None:
    t1, _ = _OTHER_TENANTS
    team = f"T{uuid.uuid4().hex[:10].upper()}"
    current = {"tid": t1}
    app, gateway = _build_app(dbs, current)
    async with _client(app) as client:
        resp = await client.post(
            "/channels/mappings", json={"channel_type": "slack", "channel_id": team}
        )
        assert resp.status_code == 200, resp.text
        claim = resp.json()
        assert claim["status"] == "pending_verification"
        code = claim["verification_code"]

        # Hashed at rest: the plaintext code is nowhere in the row.
        async with dbs.admin() as s:
            row = (
                await s.execute(
                    text("SELECT * FROM channel_tenant_mappings WHERE id = :i"),
                    {"i": claim["id"]},
                )
            ).mappings().one()
        assert code not in json.dumps({k: str(v) for k, v in row.items()})
        assert row["verification_expires_at"] is not None

        # Pending → not routed.
        assert await _resolve(dbs, team) is None
        await _slack_message(client, team, "hello")
        gateway.ingest.assert_not_called()

        # A wrong code, or the right code on another workspace, proves nothing.
        await _slack_message(client, team, "AV-ABCDEFGH")
        await _slack_message(client, f"{team}X", f"verify {code}")
        assert await _resolve(dbs, team) is None

        # The code posted in the workspace itself verifies — and is consumed.
        resp = await _slack_message(client, team, f"verify {code.lower()} please")
        assert resp.status_code == 200
        gateway.ingest.assert_not_called()
        assert await _resolve(dbs, team) == t1
        listed = {m["channel_id"]: m for m in (await client.get("/channels/mappings")).json()}
        assert listed[team]["status"] == "verified"
        assert listed[team]["verified_at"]

        # One-time: replaying the code changes nothing; normal traffic now routes.
        await _slack_message(client, team, "hi again")
        assert gateway.ingest.await_args.kwargs["tenant_id"] == t1


@pytest.mark.asyncio
async def test_second_tenant_cannot_claim_a_verified_channel(dbs: SimpleNamespace) -> None:
    t1, t2 = _OTHER_TENANTS
    team = f"T{uuid.uuid4().hex[:10].upper()}"
    current = {"tid": t1}
    app, _ = _build_app(dbs, current)
    async with _client(app) as client:
        # Two tenants may hold pending claims at once (no squatting) ...
        code1 = (
            await client.post(
                "/channels/mappings", json={"channel_type": "slack", "channel_id": team}
            )
        ).json()["verification_code"]
        current["tid"] = t2
        resp = await client.post(
            "/channels/mappings", json={"channel_type": "slack", "channel_id": team}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending_verification"

        # ... the first to prove ownership wins; the rival claim is dropped.
        await _slack_message(client, team, code1)
        assert await _resolve(dbs, team) == t1
        assert (await client.get("/channels/mappings")).json() == []

        resp = await client.post(
            "/channels/mappings", json={"channel_type": "slack", "channel_id": team}
        )
        assert resp.status_code == 409
        assert await _resolve(dbs, team) == t1

        # Re-posting by the owner is idempotent and issues no new code.
        current["tid"] = t1
        resp = await client.post(
            "/channels/mappings", json={"channel_type": "slack", "channel_id": team}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "verified"
        assert resp.json()["verification_code"] is None


@pytest.mark.asyncio
async def test_legacy_mapping_verify_action_keeps_routing_then_verifies(
    dbs: SimpleNamespace,
) -> None:
    team = f"T{uuid.uuid4().hex[:10].upper()}"
    async with dbs.admin() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO channel_tenant_mappings "
                "(id, tenant_id, channel_type, channel_id, status) "
                "VALUES (:i, :t, 'slack', :c, 'legacy_unverified')"
            ),
            {"i": uuid.uuid4().hex, "t": _LEGACY_TENANT, "c": team},
        )
    current = {"tid": _LEGACY_TENANT}
    app, _ = _build_app(dbs, current)
    async with _client(app) as client:
        mapping = next(
            m for m in (await client.get("/channels/mappings")).json() if m["channel_id"] == team
        )
        resp = await client.post(f"/channels/mappings/{mapping['id']}/verify")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "legacy_unverified"
        code = resp.json()["verification_code"]
        assert await _resolve(dbs, team) == _LEGACY_TENANT  # still routing

        await _slack_message(client, team, code)
        listed = {m["channel_id"]: m for m in (await client.get("/channels/mappings")).json()}
        assert listed[team]["status"] == "verified"

        # Another tenant cannot use the verify action on it.
        current["tid"] = _OTHER_TENANTS[0]
        assert (await client.post(f"/channels/mappings/{mapping['id']}/verify")).status_code == 404


@pytest.mark.asyncio
async def test_proof_from_the_channel_supersedes_a_rival_legacy_claim(
    dbs: SimpleNamespace,
) -> None:
    t1, _ = _OTHER_TENANTS
    team = f"T{uuid.uuid4().hex[:10].upper()}"
    async with dbs.admin() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO channel_tenant_mappings "
                "(id, tenant_id, channel_type, channel_id, status) "
                "VALUES (:i, :t, 'slack', :c, 'legacy_unverified')"
            ),
            {"i": uuid.uuid4().hex, "t": _LEGACY_TENANT, "c": team},
        )
    current = {"tid": t1}
    app, _ = _build_app(dbs, current)
    async with _client(app) as client:
        code = (
            await client.post(
                "/channels/mappings", json={"channel_type": "slack", "channel_id": team}
            )
        ).json()["verification_code"]
        assert await _resolve(dbs, team) == _LEGACY_TENANT
        await _slack_message(client, team, code)
        assert await _resolve(dbs, team) == t1

        # The rival legacy mapping is kept for audit as "superseded" (no routing),
        # not deleted, and the displacement is recorded on its tenant's trail.
        current["tid"] = _LEGACY_TENANT
        listed = {m["channel_id"]: m for m in (await client.get("/channels/mappings")).json()}
        assert listed[team]["status"] == "superseded"
    audit = await _audit_rows(dbs, _LEGACY_TENANT, "channel_mapping.superseded")
    assert any(team in (r["note"] or "") for r in audit)


@pytest.mark.asyncio
async def test_expired_code_does_not_verify(dbs: SimpleNamespace) -> None:
    t1, _ = _OTHER_TENANTS
    team = f"T{uuid.uuid4().hex[:10].upper()}"
    current = {"tid": t1}
    app, _ = _build_app(dbs, current)
    async with _client(app) as client:
        claim = (
            await client.post(
                "/channels/mappings", json={"channel_type": "slack", "channel_id": team}
            )
        ).json()
        async with dbs.admin() as s, s.begin():
            await s.execute(
                text(
                    "UPDATE channel_tenant_mappings SET verification_expires_at = "
                    "now() - interval '1 minute' WHERE id = :i"
                ),
                {"i": claim["id"]},
            )
        await _slack_message(client, team, claim["verification_code"])
        assert await _resolve(dbs, team) is None


# ── operator approval (sms / email: a code proves sending, not owning) ─────


async def _audit_rows(dbs: SimpleNamespace, tenant_id: str, tool_name: str) -> list[Any]:
    async with dbs.admin() as s:
        rows = await s.execute(
            text(
                "SELECT tool_name, outcome, approver, note FROM audit_log "
                "WHERE tenant_id = :t AND tool_name = :n"
            ),
            {"t": tenant_id, "n": tool_name},
        )
        return list(rows.mappings())


async def _sms_resolve(dbs: SimpleNamespace, number: str) -> str | None:
    from app.api.channels.ingestion import _resolve_tenant_from_channel

    return await _resolve_tenant_from_channel("sms", number, dbs.admin)


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Key": _ADMIN_KEY}


@pytest.mark.asyncio
async def test_migration_moves_pending_send_only_claims_to_the_operator_queue(
    dbs: SimpleNamespace,
) -> None:
    async with dbs.admin() as s:
        row = (
            await s.execute(
                text(
                    "SELECT status, verification_code_hash FROM channel_tenant_mappings "
                    "WHERE id = 'pending-sms-1'"
                )
            )
        ).one()
    assert row[0] == "pending_operator_approval"
    assert row[1] is None  # the outstanding code can no longer verify anything


@pytest.mark.asyncio
async def test_sms_claim_waits_for_an_operator_and_cannot_be_self_verified(
    dbs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.channels import verification

    monkeypatch.setenv("PLATFORM_ADMIN_KEY", _ADMIN_KEY)
    t1, t2 = _OTHER_TENANTS
    number = f"+1555{uuid.uuid4().int % 10**7:07d}"
    current = {"tid": t1}
    app, _ = _build_app(dbs, current)
    async with _client(app) as client:
        resp = await client.post(
            "/channels/mappings", json={"channel_type": "sms", "channel_id": number}
        )
        assert resp.status_code == 200, resp.text
        claim = resp.json()
        assert claim["status"] == "pending_operator_approval"
        assert claim["verification_code"] is None
        assert "awaiting operator approval" in claim["instructions"].lower()
        assert await _sms_resolve(dbs, number) is None

        # No self-verification: the verify action refuses, and a code "sent to
        # the number" (which anyone can do) verifies nothing.
        resp = await client.post(f"/channels/mappings/{claim['id']}/verify")
        assert resp.status_code == 409
        assert "operator approval" in resp.json()["detail"].lower()
        assert (
            await verification.verify_from_inbound(dbs.admin, "sms", number, "AV-ABCDEFGH")
            is None
        )
        assert await _sms_resolve(dbs, number) is None

        # A rival claim on the same number also queues (no squatting).
        current["tid"] = t2
        rival = (
            await client.post(
                "/channels/mappings", json={"channel_type": "sms", "channel_id": number}
            )
        ).json()
        assert rival["status"] == "pending_operator_approval"

        # The operator sees both claims and approves t1's.
        review = await client.get("/admin/channel-mappings/review", headers=_admin_headers())
        assert review.status_code == 200, review.text
        queued = {
            (m["tenant_id"], m["status"])
            for m in review.json()["mappings"]
            if m["channel_id"] == number
        }
        assert queued == {(t1, "pending_operator_approval"), (t2, "pending_operator_approval")}

        resp = await client.post(
            f"/admin/channel-mappings/{claim['id']}/approve",
            json={"operator": "alice", "reason": "carrier invoice checked"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "verified"
        assert await _sms_resolve(dbs, number) == t1

        # The rival never routed and is dropped; approving again is refused.
        assert (await client.get("/channels/mappings")).json() == []
        again = await client.post(
            f"/admin/channel-mappings/{claim['id']}/approve", headers=_admin_headers()
        )
        assert again.status_code == 409
    audit = await _audit_rows(dbs, t1, "channel_mapping.operator_approved")
    assert len(audit) == 1
    assert audit[0]["outcome"] == "approved"
    assert "alice" in audit[0]["approver"]
    assert number in audit[0]["note"] and "carrier invoice checked" in audit[0]["note"]


@pytest.mark.asyncio
async def test_operator_rejects_an_email_claim(
    dbs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLATFORM_ADMIN_KEY", _ADMIN_KEY)
    t1, _ = _OTHER_TENANTS
    address = f"ops-{uuid.uuid4().hex[:8]}@victim.test"
    current = {"tid": t1}
    app, _ = _build_app(dbs, current)
    async with _client(app) as client:
        claim = (
            await client.post(
                "/channels/mappings", json={"channel_type": "email", "channel_id": address}
            )
        ).json()
        assert claim["status"] == "pending_operator_approval"
        resp = await client.post(
            f"/admin/channel-mappings/{claim['id']}/reject",
            json={"operator": "bob", "reason": "not their domain"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "rejected"
        listed = {m["channel_id"]: m for m in (await client.get("/channels/mappings")).json()}
        assert listed[address]["status"] == "rejected"
        assert (
            await client.post(
                f"/admin/channel-mappings/{claim['id']}/approve", headers=_admin_headers()
            )
        ).status_code == 409
        assert (
            await client.post(
                "/admin/channel-mappings/does-not-exist/reject", headers=_admin_headers()
            )
        ).status_code == 404
    from app.api.channels.ingestion import _resolve_tenant_from_channel

    assert await _resolve_tenant_from_channel("email", address, dbs.admin) is None
    audit = await _audit_rows(dbs, t1, "channel_mapping.operator_rejected")
    assert len(audit) == 1 and "not their domain" in audit[0]["note"]


@pytest.mark.asyncio
async def test_legacy_email_mapping_keeps_routing_and_is_listed_for_review(
    dbs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.channels.ingestion import _resolve_tenant_from_channel

    monkeypatch.setenv("PLATFORM_ADMIN_KEY", _ADMIN_KEY)
    current = {"tid": _LEGACY_TENANT}
    app, _ = _build_app(dbs, current)
    async with _client(app) as client:
        assert await _resolve_tenant_from_channel("email", _LEGACY_EMAIL, dbs.admin) == (
            _LEGACY_TENANT
        )
        review = (
            await client.get("/admin/channel-mappings/review", headers=_admin_headers())
        ).json()["mappings"]
        entries = [m for m in review if m["channel_id"] == _LEGACY_EMAIL]
        assert [(m["tenant_id"], m["status"]) for m in entries] == [
            (_LEGACY_TENANT, "legacy_unverified")
        ]
        # A legacy Slack mapping is verified by code, not by an operator.
        assert all(m["channel_type"] != "slack" for m in review)

        # The tenant cannot self-verify it either.
        mapping_id = entries[0]["id"]
        assert (await client.post(f"/channels/mappings/{mapping_id}/verify")).status_code == 409


@pytest.mark.asyncio
async def test_migration_downgrades_and_reupgrades(postgres_url: str) -> None:
    """Runs last: the downgrade drops pending claims and restores the old index;
    re-upgrading turns every surviving mapping into a routable legacy one."""
    await asyncio.to_thread(_alembic, postgres_url, "downgrade", _PREVIOUS_HEAD)
    await asyncio.to_thread(_alembic, postgres_url, "upgrade", "head")
    engine = create_async_engine(postgres_url)
    try:
        async with engine.connect() as conn:
            statuses = {
                r[0]
                for r in await conn.execute(
                    text("SELECT DISTINCT status FROM channel_tenant_mappings")
                )
            }
    finally:
        await engine.dispose()
    assert statuses == {"legacy_unverified"}
