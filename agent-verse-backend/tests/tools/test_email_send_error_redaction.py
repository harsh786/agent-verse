"""NATIVE-05: platform SMTP failure detail is logged server-side, never returned.

The raw relay exception (host, port, auth failure text) went back to the tenant
in the 500 body.
"""

from __future__ import annotations

import sys
import uuid
from types import ModuleType
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext

SECRET_DETAIL = "535 5.7.8 authentication failed for relay@smtp.internal.corp:587"


@pytest.fixture
def failing_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = ModuleType("aiosmtplib")

    async def _send(*_a: Any, **_k: Any) -> None:
        raise RuntimeError(SECRET_DETAIL)

    mod.send = _send  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aiosmtplib", mod)


async def test_email_send_returns_a_generic_error_with_a_correlation_id(
    failing_smtp: None,
) -> None:
    from app.tools.email_tool import email_send

    res = await email_send("a@example.com", "s", "b", tenant_id="t")
    assert res["success"] is False
    assert SECRET_DETAIL not in res["error"] and "smtp.internal" not in res["error"]
    assert res["error_id"] and res["error_id"] in res["error"]


def test_api_answers_502_without_relay_detail(failing_smtp: None) -> None:
    from app.api.tools import router

    ctx = TenantContext(
        tenant_id=f"t-{uuid.uuid4().hex[:8]}", plan=PlanTier.FREE, api_key_id="k",
        roles=("operator",),
    )
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    app.state.audit_log = AuditLog()
    resp = TestClient(app).post(
        "/tools/email/send", json={"to": "a@example.com", "subject": "s", "body": "b"}
    )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "smtp.internal" not in detail and "535" not in detail
    assert "email delivery failed" in detail.lower()
