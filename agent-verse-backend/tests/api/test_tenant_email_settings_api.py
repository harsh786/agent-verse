"""a02-F036-02: tenant email settings API + the agent email tool's relay choice.

(a) optional per-tenant recipient allowlist: empty = today's behaviour; non-empty
    = a recipient outside it refuses the whole message (403, nothing sent, no quota).
(d) tenant-owned SMTP sender: when configured the agent email tool sends through
    it (quota, audit and allowlist still apply); otherwise the platform relay.
    Its secret is vault-encrypted and never returned. System mail (the platform
    relay function) never reads tenant settings.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext
from app.tools import email_tool, tenant_smtp

_REAL_CHECK_HOST = tenant_smtp.check_host


class _QuotaRedis:
    """The quota Lua script's semantics (fakeredis has no EVAL)."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    # Redis EVAL (a server-side Lua script), not Python eval.
    async def eval(self, _script: str, _n: int, key: str, n: str, limit: str, _ttl: str) -> Any:
        used = self.counts.get(key, 0)
        if used + int(n) > int(limit):
            return [0, used]
        self.counts[key] = used + int(n)
        return [1, used + int(n)]


def _app(roles: tuple[str, ...] = ("admin",)) -> tuple[FastAPI, TenantContext]:
    from app.api.tenant_email import router as email_router
    from app.api.tools import router as tools_router

    ctx = TenantContext(
        tenant_id=f"t-{uuid.uuid4().hex[:8]}", plan=PlanTier.FREE, api_key_id="k1", roles=roles
    )
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        # One tenant; the key's roles come from a test header (default: *roles*).
        header = request.headers.get("x-test-roles")
        key_roles = tuple(header.split(",")) if header else roles
        request.state.tenant = TenantContext(
            tenant_id=ctx.tenant_id, plan=ctx.plan, api_key_id=ctx.api_key_id, roles=key_roles
        )
        return await call_next(request)

    app.include_router(tools_router)
    app.include_router(email_router)
    app.state.audit_log = AuditLog()
    app.state._redis = _QuotaRedis()
    return app, ctx


def _as(app: FastAPI, ctx: TenantContext, roles: tuple[str, ...]) -> TestClient:
    """The same app (same tenant, same stored settings) through a key with *roles*."""
    return TestClient(app, headers={"x-test-roles": ",".join(roles)})


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Both relays faked; records which one was used and with what."""
    log: dict[str, list[Any]] = {"platform": [], "tenant": []}

    async def _platform(to: Any, subject: str, body: str, **_kw: Any) -> dict[str, Any]:
        log["platform"].append(to)
        return {"success": True, "to": to, "subject": subject}

    async def _deliver(target: Any, secret: Any, msg: Any, recipients: list[str], **_k: Any) -> Any:
        log["tenant"].append(
            {"target": target, "secret": secret, "recipients": recipients, "from": msg["From"]}
        )
        return tenant_smtp.SMTPOutcome(True, "done", "sent")

    monkeypatch.setattr("app.tools.email_tool.email_send", _platform)
    monkeypatch.setattr("app.tools.tenant_smtp.deliver", _deliver)
    # DNS-free egress check for the example hosts used here.
    monkeypatch.setattr(tenant_smtp, "check_host", lambda host: ["203.0.113.10"])
    return log


def _smtp_body(secret: str | None, **over: Any) -> dict[str, Any]:
    body = {
        "host": "smtp.tenant.test",
        "port": 587,
        "tls_mode": "starttls",
        "username": "mailer@tenant.test",
        "from_address": "bot@tenant.test",
        "secret": secret,
    }
    body.update(over)
    return body


def _send(client: TestClient, to: Any) -> Any:
    return client.post("/tools/email/send", json={"to": to, "subject": "s", "body": "b"})


def _audit_rows(app: FastAPI, ctx: TenantContext, tool: str) -> list[Any]:
    return list(app.state.audit_log.query(tenant_ctx=ctx, tool_name=tool))


# ── allowlist ────────────────────────────────────────────────────────────────


def test_empty_allowlist_keeps_todays_behaviour(sent: dict[str, list[Any]]) -> None:
    app, _ = _app(("operator",))
    resp = _send(TestClient(app), ["anyone@anywhere.test"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["relay"] == "platform"
    assert sent["platform"] == [["anyone@anywhere.test"]]


def test_allowlist_refuses_the_whole_message_and_consumes_no_quota(
    sent: dict[str, list[Any]],
) -> None:
    app, ctx = _app()
    admin = TestClient(app)
    put = admin.put(
        "/tenants/me/email/allowlist", json={"entries": ["partner.io", "Boss@Example.com"]}
    )
    assert put.status_code == 200, put.text
    assert put.json()["recipient_allowlist"] == ["partner.io", "boss@example.com"]

    operator = _as(app, ctx, ("operator",))
    denied = _send(operator, ["a@partner.io", "x@elsewhere.test"])
    assert denied.status_code == 403
    assert "x@elsewhere.test" in denied.json()["detail"]
    assert "a@partner.io" not in denied.json()["detail"]
    assert sent["platform"] == []  # nothing sent, not even to the allowed one
    assert app.state._redis.counts == {}  # no quota consumed
    rejected = _audit_rows(app, ctx, "email.send")
    assert [e.outcome for e in rejected] == ["rejected"]
    assert "recipient_allowlist" in rejected[0].note

    ok = _send(operator, ["a@partner.io", "boss@example.com"])
    assert ok.status_code == 200, ok.text
    assert sent["platform"] == [["a@partner.io", "boss@example.com"]]


def test_allowlist_update_is_validated_audited_and_admin_only(
    sent: dict[str, list[Any]],
) -> None:
    app, ctx = _app()
    admin = TestClient(app)
    assert (
        admin.put("/tenants/me/email/allowlist", json={"entries": ["not a domain"]}).status_code
        == 422
    )
    assert admin.put("/tenants/me/email/allowlist", json={"entries": ["a.io"]}).status_code == 200
    rows = _audit_rows(app, ctx, "tenant.email_allowlist")
    assert len(rows) == 1 and rows[0].goal_id == "tenant_settings" and "entries=1" in rows[0].note
    # Clearing it turns the restriction off again.
    cleared = admin.put("/tenants/me/email/allowlist", json={"entries": []})
    assert cleared.json()["recipient_allowlist"] == []

    operator = _as(app, ctx, ("operator",))
    assert operator.get("/tenants/me/email").status_code == 403
    assert operator.put("/tenants/me/email/allowlist", json={"entries": []}).status_code == 403
    assert operator.put("/tenants/me/email/smtp", json=_smtp_body("x")).status_code == 403
    assert operator.delete("/tenants/me/email/smtp").status_code == 403
    assert operator.post("/tenants/me/email/smtp/test", json={}).status_code == 403


# ── tenant SMTP ──────────────────────────────────────────────────────────────


def test_smtp_secret_is_encrypted_and_never_echoed(sent: dict[str, list[Any]]) -> None:
    app, ctx = _app()
    admin = TestClient(app)
    secret = secrets.token_urlsafe(24)
    put = admin.put("/tenants/me/email/smtp", json=_smtp_body(secret))
    assert put.status_code == 200, put.text
    view = admin.get("/tenants/me/email")
    for resp in (put, view):
        assert secret not in resp.text
        smtp = resp.json()["smtp"]
        assert smtp["secret_set"] is True and smtp["secret_masked"] == "********"
        assert "secret_enc" not in smtp and resp.json()["relay"] == "tenant"
    stored = app.state.tenant_email_settings_memory[ctx.tenant_id].smtp
    assert stored.secret_enc and secret not in stored.secret_enc  # vault ciphertext
    note = _audit_rows(app, ctx, "tenant.email_smtp")[0].note
    assert secret not in note and "secret_changed=True" in note


def test_agent_email_uses_the_tenant_smtp_with_quota_audit_and_allowlist(
    sent: dict[str, list[Any]],
) -> None:
    app, ctx = _app()
    admin = TestClient(app)
    secret = secrets.token_urlsafe(24)
    assert admin.put("/tenants/me/email/smtp", json=_smtp_body(secret)).status_code == 200
    assert (
        admin.put("/tenants/me/email/allowlist", json={"entries": ["partner.io"]}).status_code
        == 200
    )

    operator = _as(app, ctx, ("operator",))
    resp = _send(operator, "a@partner.io")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["relay"] == "tenant" and body["quota_remaining"] == 99
    assert sent["platform"] == []
    [call] = sent["tenant"]
    assert call["secret"] == secret  # decrypted from the vault for the send only
    assert call["target"].host == "smtp.tenant.test" and call["from"] == "bot@tenant.test"
    assert secret not in resp.text
    requested = [e for e in _audit_rows(app, ctx, "email.send") if e.outcome == "requested"]
    assert len(requested) == 1 and "relay=tenant" in requested[0].note

    # The allowlist still applies to the tenant sender.
    assert _send(operator, "x@elsewhere.test").status_code == 403
    assert len(sent["tenant"]) == 1

    # Removing it falls back to the platform relay.
    assert admin.delete("/tenants/me/email/smtp").json()["relay"] == "platform"
    again = _send(operator, "a@partner.io")
    assert again.status_code == 200 and again.json()["relay"] == "platform"
    assert sent["platform"] == ["a@partner.io"]
    assert app.state.tenant_email_settings_memory[ctx.tenant_id].smtp is None


def test_unreadable_tenant_secret_never_falls_back_to_the_platform(
    sent: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, ctx = _app()
    admin = TestClient(app)
    assert (
        admin.put("/tenants/me/email/smtp", json=_smtp_body(secrets.token_urlsafe(24))).status_code
        == 200
    )

    async def _broken(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("vault key mismatch")

    monkeypatch.setattr("app.providers.tenant_vault.decrypt_tenant_secret", _broken)
    resp = _send(_as(app, ctx, ("operator",)), "a@partner.io")
    assert resp.status_code == 503
    assert sent["platform"] == [] and sent["tenant"] == []


def test_stored_secret_is_reused_only_for_the_same_server(sent: dict[str, list[Any]]) -> None:
    app, ctx = _app()
    admin = TestClient(app)
    secret = secrets.token_urlsafe(24)
    assert admin.put("/tenants/me/email/smtp", json=_smtp_body(secret)).status_code == 200
    first = app.state.tenant_email_settings_memory[ctx.tenant_id].smtp.secret_enc

    # Same host/port/username, no secret: kept (e.g. only the from-address changed).
    keep = admin.put(
        "/tenants/me/email/smtp", json=_smtp_body(None, from_address="ops@tenant.test")
    )
    assert keep.status_code == 200, keep.text
    assert app.state.tenant_email_settings_memory[ctx.tenant_id].smtp.secret_enc == first

    # Another host (or port, or username) without a secret: refused.
    for change in ({"host": "smtp.attacker.test"}, {"port": 465}, {"username": "other"}):
        resp = admin.put("/tenants/me/email/smtp", json=_smtp_body(None, **change))
        assert resp.status_code == 422, change
        assert "secret is required" in resp.json()["detail"]

    # No username = no AUTH: no secret stored.
    anon = admin.put("/tenants/me/email/smtp", json=_smtp_body(None, username=""))
    assert anon.status_code == 200 and anon.json()["smtp"]["secret_set"] is False


def test_smtp_config_is_validated_against_the_egress_policy(
    sent: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, ctx = _app()
    admin = TestClient(app)
    monkeypatch.setattr(tenant_smtp, "check_host", _REAL_CHECK_HOST)
    secret = secrets.token_urlsafe(24)
    bad = [
        {"host": "169.254.169.254"},  # cloud metadata: never reachable
        {"port": 22},  # not an SMTP port
        {"from_address": "not-an-address"},
        {"host": "smtp.tenant.test:25"},
    ]
    for change in bad:
        resp = admin.put("/tenants/me/email/smtp", json=_smtp_body(secret, **change))
        assert resp.status_code == 422, (change, resp.text)
    assert admin.get("/tenants/me/email").json()["smtp"] is None
    assert _audit_rows(app, ctx, "tenant.email_smtp") == []


def test_test_endpoint_reports_the_stage_and_hides_the_secret(
    sent: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, ctx = _app()
    admin = TestClient(app)
    assert admin.post("/tenants/me/email/smtp/test", json={}).status_code == 404
    secret = secrets.token_urlsafe(24)
    assert admin.put("/tenants/me/email/smtp", json=_smtp_body(secret)).status_code == 200
    seen: list[Any] = []

    async def _probe(
        target: Any, sec: Any, *, message: Any = None, recipients: Any = None, **_k: Any
    ) -> Any:
        seen.append((target.host, sec, recipients))
        return tenant_smtp.SMTPOutcome(False, "auth", "authentication failed", 535)

    monkeypatch.setattr(tenant_smtp, "probe", _probe)
    resp = admin.post("/tenants/me/email/smtp/test", json={})
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": False,
        "stage": "auth",
        "message": "authentication failed",
        "smtp_code": 535,
        "tested": "saved",
    }
    assert seen == [("smtp.tenant.test", secret, [])]
    assert secret not in resp.text

    # A candidate on another host never receives the stored secret.
    cand = admin.post(
        "/tenants/me/email/smtp/test",
        json={"config": _smtp_body(None, host="smtp.other.test")},
    )
    assert cand.status_code == 200 and cand.json()["tested"] == "candidate"
    assert seen[-1] == ("smtp.other.test", None, [])
    audit = _audit_rows(app, ctx, "tenant.email_smtp_test")
    assert [e.outcome for e in audit] == ["requested", "failed", "requested", "failed"]
    assert all(secret not in e.note for e in audit)

    # A test send obeys the allowlist.
    assert (
        admin.put("/tenants/me/email/allowlist", json={"entries": ["partner.io"]}).status_code
        == 200
    )
    blocked = admin.post("/tenants/me/email/smtp/test", json={"send_to": "x@elsewhere.test"})
    assert blocked.status_code == 403
    allowed = admin.post("/tenants/me/email/smtp/test", json={"send_to": "a@partner.io"})
    assert allowed.status_code == 200 and seen[-1][2] == ["a@partner.io"]


async def test_platform_relay_never_reads_tenant_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """System mail path: ``email_send`` is the platform relay whatever the tenant set."""
    calls: list[dict[str, Any]] = []

    async def _aiosmtplib_send(msg: Any, **kw: Any) -> Any:
        calls.append({"from": msg["From"], **kw})
        return ({}, "ok")

    def _forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the platform relay must not read tenant email settings")

    import aiosmtplib

    monkeypatch.setattr(aiosmtplib, "send", _aiosmtplib_send)
    monkeypatch.setattr("app.services.tenant_email_settings.email_settings_for", _forbidden)
    monkeypatch.setattr("app.tools.tenant_smtp.deliver", _forbidden)
    monkeypatch.setenv("SMTP_HOST", "relay.platform.test")
    monkeypatch.setenv("SMTP_FROM", "noreply@platform.test")
    result = await email_tool.email_send("user@example.com", "Invite", "b", tenant_id="t-x")
    assert result["success"] is True
    assert calls[0]["hostname"] == "relay.platform.test"
    assert calls[0]["from"] == "noreply@platform.test"
