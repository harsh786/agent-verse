"""NATIVE-03: email sends and workspace writes/deletes are durably audited.

They left no audit record, so platform-sender abuse and data deletion were
untraceable. A durable row is committed BEFORE the side effect (fail closed: no
row, no send/write/delete -> 503); a failed operation adds an outcome row.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext


class _FailingDb:
    def __call__(self) -> Any:
        session = MagicMock()
        session.__aenter__ = AsyncMock(side_effect=ConnectionError("db down"))
        session.__aexit__ = AsyncMock(return_value=False)
        return session


def _app(audit: AuditLog, tenant: str) -> TestClient:
    from app.api.tools import router

    ctx = TenantContext(
        tenant_id=tenant, plan=PlanTier.FREE, api_key_id="key-9", roles=("admin",)
    )
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    app.state.audit_log = audit
    return TestClient(app)


def _tenant() -> TenantContext:
    return TenantContext(tenant_id=f"t-{uuid.uuid4().hex[:8]}", plan=PlanTier.FREE, api_key_id="")


def test_write_and_delete_are_audited() -> None:
    audit, t = AuditLog(), _tenant()
    client = _app(audit, t.tenant_id)
    assert client.post("/tools/files/a/b.txt", json={"content": "hello"}).status_code == 201
    assert client.delete("/tools/files/a/b.txt").status_code == 204
    events = audit.query(tenant_ctx=t)
    assert [e.tool_name for e in events] == ["workspace.write", "workspace.delete"]
    assert all(e.api_key_id == "key-9" for e in events)
    assert "path=a/b.txt" in events[0].note and "bytes=5" in events[0].note


def test_write_is_refused_when_the_audit_cannot_be_committed() -> None:
    t = _tenant()
    client = _app(AuditLog(db_session_factory=_FailingDb()), t.tenant_id)
    resp = client.post("/tools/files/x.txt", json={"content": "secret"})
    assert resp.status_code == 503
    # Nothing was written: the audit row is the precondition.
    ok = _app(AuditLog(), t.tenant_id)
    assert ok.get("/tools/files/x.txt").status_code == 404


def test_delete_is_refused_when_the_audit_cannot_be_committed() -> None:
    t = _tenant()
    ok = _app(AuditLog(), t.tenant_id)
    assert ok.post("/tools/files/keep.txt", json={"content": "k"}).status_code == 201
    failing = _app(AuditLog(db_session_factory=_FailingDb()), t.tenant_id)
    assert failing.delete("/tools/files/keep.txt").status_code == 503
    assert ok.get("/tools/files/keep.txt").status_code == 200


def test_email_send_is_audited_with_hashed_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []

    async def _send(to: Any, subject: str, body: str, **_kw: Any) -> dict[str, Any]:
        sent.append(to)
        return {"success": True, "to": to, "subject": subject}

    monkeypatch.setattr("app.tools.email_tool.email_send", _send)
    audit, t = AuditLog(), _tenant()
    client = _app(audit, t.tenant_id)
    resp = client.post(
        "/tools/email/send",
        json={"to": ["a@example.com", "b@example.com"], "subject": "Quarterly", "body": "x"},
    )
    assert resp.status_code == 200, resp.text
    [ev] = audit.query(tenant_ctx=t)
    assert ev.tool_name == "email.send"
    assert "recipients=2" in ev.note and "recipients_sha256=" in ev.note
    assert "subject_sha256=" in ev.note
    assert "a@example.com" not in ev.note and "Quarterly" not in ev.note


def test_email_is_not_sent_when_the_audit_cannot_be_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("app.tools.email_tool.email_send", send)
    client = _app(AuditLog(db_session_factory=_FailingDb()), _tenant().tenant_id)
    resp = client.post(
        "/tools/email/send", json={"to": "a@example.com", "subject": "s", "body": "b"}
    )
    assert resp.status_code == 503
    send.assert_not_called()


def test_failed_email_adds_an_outcome_row(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"success": False, "error": "smtp down"}

    monkeypatch.setattr("app.tools.email_tool.email_send", _fail)
    audit, t = AuditLog(), _tenant()
    client = _app(audit, t.tenant_id)
    client.post("/tools/email/send", json={"to": "a@example.com", "subject": "s", "body": "b"})
    outcomes = [e.outcome for e in audit.query(tenant_ctx=t)]
    assert outcomes == ["requested", "failed"]
