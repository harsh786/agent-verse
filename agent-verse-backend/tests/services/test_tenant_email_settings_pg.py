"""a02-F036-02 on real Postgres (least-privilege NOBYPASSRLS role).

``tenant_email_settings`` is FORCE RLS: a tenant reads and writes only its own
row, and nothing is visible without a tenant context. The SMTP secret is stored
as vault ciphertext only, re-encrypted by the vault rotation. End to end, the
agent email tool sends through the tenant's own SMTP server (an in-process
server) with the allowlist applied, and through nothing else.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_tenant_email_settings_pg.py -m integration
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db.rls import sqlalchemy_rls_context
from app.governance.audit import AuditLog
from app.services.tenant_email_settings import (
    TenantEmailSettingsStore,
    TenantSMTPSettings,
    open_smtp_secret,
    seal_smtp_secret,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tools import tenant_smtp
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions
from tests.tools._smtp_server import FakeSMTPServer

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


async def test_settings_are_tenant_isolated_and_the_secret_is_ciphertext(pg_url: str) -> None:
    ta, tb, tc = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    for tid in (ta, tb, tc):
        await seed_tenant(pg_url, tid)
    engine = await app_engine(pg_url)
    try:
        db = sessions(engine)
        store = TenantEmailSettingsStore(db=db)
        secret = secrets.token_urlsafe(24)
        sealed, fingerprint = await seal_smtp_secret(db, ta, secret)
        await store.set_allowlist(ta, ["partner.io", "*.corp.example"], updated_by="k-a")
        await store.set_smtp(
            ta,
            TenantSMTPSettings(
                host="smtp.tenant-a.test",
                port=587,
                tls_mode="starttls",
                username="mailer",
                from_address="bot@tenant-a.test",
                secret_enc=sealed,
                vault_key_fingerprint=fingerprint,
            ),
            updated_by="k-a",
        )

        a = await store.get(ta)
        assert a.recipient_allowlist == ["partner.io", "*.corp.example"]
        assert a.smtp is not None and a.smtp.host == "smtp.tenant-a.test"
        assert a.updated_by == "k-a" and a.updated_at
        assert await open_smtp_secret(db, ta, a.smtp) == secret
        assert "secret_enc" not in str(a.public_view()) and secret not in str(a.public_view())

        # Tenant B sees defaults, not A's row.
        b = await store.get(tb)
        assert b.recipient_allowlist == [] and b.smtp is None

        # RLS on the raw table: B's context sees nothing of A, and cannot write
        # another tenant's row (C has none yet, so only the policy can refuse it).
        async with db() as s, s.begin(), sqlalchemy_rls_context(s, tb):
            rows = (await s.execute(text("SELECT tenant_id FROM tenant_email_settings"))).all()
            assert rows == []
        with pytest.raises(Exception, match="row-level security"):
            async with db() as s, s.begin(), sqlalchemy_rls_context(s, tb):
                await s.execute(
                    text("INSERT INTO tenant_email_settings (tenant_id) VALUES (:t)"), {"t": tc}
                )
        async with db() as s, s.begin():  # no tenant context at all
            assert (await s.execute(text("SELECT 1 FROM tenant_email_settings"))).all() == []

        # At rest: ciphertext only.
        [(stored,)] = await admin_exec(
            pg_url,
            "SELECT smtp_secret_enc FROM tenant_email_settings WHERE tenant_id = :t",
            {"t": ta},
        )
        assert stored == sealed and secret not in stored

        # The vault rotation re-encrypts it (store registered over the real schema).
        from app.providers.vault import CredentialVault, get_vault
        from app.providers.vault_rotation import PG_STORES, StoreReport, _rotate_pg_batch

        new = CredentialVault(secrets.token_urlsafe(32))
        smtp_store = next(s for s in PG_STORES if s.name == "tenant_smtp_secrets")
        report, _ = await _rotate_pg_batch(db, smtp_store, ta, "", 50, get_vault(), new, False)
        assert isinstance(report, StoreReport) and report.rotated == 1 and report.failed == 0
        [(rotated, fp)] = await admin_exec(
            pg_url,
            "SELECT smtp_secret_enc, vault_key_fingerprint FROM tenant_email_settings "
            "WHERE tenant_id = :t",
            {"t": ta},
        )
        assert new.decrypt(rotated) == secret and fp == new.fingerprint()

        # Removing the sender deletes the ciphertext and keeps the allowlist.
        cleared = await store.clear_smtp(ta, updated_by="k-a")
        assert cleared.smtp is None and cleared.recipient_allowlist == [
            "partner.io",
            "*.corp.example",
        ]
        [(gone,)] = await admin_exec(
            pg_url,
            "SELECT smtp_secret_enc FROM tenant_email_settings WHERE tenant_id = :t",
            {"t": ta},
        )
        assert gone is None
    finally:
        await engine.dispose()


async def test_agent_email_goes_through_the_tenant_smtp_end_to_end(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.tenant_email import router as email_router
    from app.api.tools import router as tools_router

    tid = uuid.uuid4().hex
    await seed_tenant(pg_url, tid)
    engine = await app_engine(pg_url)
    user, secret = "mailer", secrets.token_urlsafe(24)
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")  # loopback test server
    platform_calls: list[Any] = []

    async def _platform(*a: Any, **k: Any) -> dict[str, Any]:
        platform_calls.append(a)
        return {"success": True}

    monkeypatch.setattr("app.tools.email_tool.email_send", _platform)
    try:
        async with FakeSMTPServer(username=user, secret=secret) as server:
            monkeypatch.setattr(tenant_smtp, "allowed_ports", lambda: frozenset({server.port}))
            app = FastAPI()

            @app.middleware("http")
            async def _inject(request: Any, call_next: Any) -> Any:
                roles = tuple(request.headers.get("x-test-roles", "admin").split(","))
                request.state.tenant = TenantContext(tid, PlanTier.FREE, "k-1", roles=roles)
                return await call_next(request)

            app.include_router(tools_router)
            app.include_router(email_router)
            app.state.audit_log = AuditLog()
            app.state._redis = None  # development: process-local quota
            app.state.db_session_factory = sessions(engine)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                cfg = {
                    "host": "127.0.0.1",
                    "port": server.port,
                    "tls_mode": "none",
                    "username": user,
                    "secret": secret,
                    "from_address": "bot@tenant.test",
                }
                put = await c.put("/tenants/me/email/smtp", json=cfg)
                assert put.status_code == 200, put.text
                assert secret not in put.text and put.json()["relay"] == "tenant"
                probe = await c.post("/tenants/me/email/smtp/test", json={})
                assert probe.json()["ok"] is True, probe.text
                allow = await c.put(
                    "/tenants/me/email/allowlist", json={"entries": ["partner.test"]}
                )
                assert allow.status_code == 200

                op = {"x-test-roles": "operator"}
                ok = await c.post(
                    "/tools/email/send",
                    json={"to": ["a@partner.test"], "subject": "Hello", "body": "Body"},
                    headers=op,
                )
                assert ok.status_code == 200, ok.text
                assert ok.json()["relay"] == "tenant"
                denied = await c.post(
                    "/tools/email/send",
                    json={"to": ["a@partner.test", "x@other.test"], "subject": "s", "body": "b"},
                    headers=op,
                )
                assert denied.status_code == 403
    finally:
        await engine.dispose()

    [msg] = server.messages
    assert msg.mail_from == "bot@tenant.test" and msg.rcpt_to == ["a@partner.test"]
    assert msg.authenticated_as == user and "Subject: Hello" in msg.data
    assert platform_calls == []  # the platform relay was never used
