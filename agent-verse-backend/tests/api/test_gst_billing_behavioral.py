"""Behavioral tests for GST billing — validation and DB persistence."""
import pytest
from pydantic import ValidationError


def test_gst_rejects_negative_amount():
    from app.api.gst_billing import GSTInvoiceRequest
    with pytest.raises(ValidationError) as exc:
        GSTInvoiceRequest(amount_inr=-100, buyer_name="Test", buyer_address="Delhi")
    assert "greater than 0" in str(exc.value).lower() or "gt" in str(exc.value).lower()


def test_gst_rejects_invalid_gstin():
    from app.api.gst_billing import GSTInvoiceRequest
    with pytest.raises(ValidationError):
        GSTInvoiceRequest(amount_inr=100, buyer_name="Test", buyer_address="Delhi",
                          buyer_gstin="INVALID_GSTIN")


def test_gst_accepts_valid_gstin():
    from app.api.gst_billing import GSTInvoiceRequest
    r = GSTInvoiceRequest(amount_inr=1180, buyer_name="Acme Ltd", buyer_address="Mumbai",
                          buyer_gstin="27AABCU9603R1ZX")
    assert r.buyer_gstin == "27AABCU9603R1ZX"


def test_gst_accepts_none_gstin():
    """B2C invoices (no GSTIN) must be accepted."""
    from app.api.gst_billing import GSTInvoiceRequest
    r = GSTInvoiceRequest(amount_inr=590, buyer_name="Individual", buyer_address="Bengaluru")
    assert r.buyer_gstin is None


@pytest.mark.asyncio
async def test_gst_invoice_persisted_to_db(monkeypatch):
    """Invoice must INSERT to gst_invoices table."""
    captured = {}
    from unittest.mock import AsyncMock, MagicMock

    async def fake_execute(query, params=None):
        if "INSERT INTO gst_invoices" in str(query):
            captured["params"] = params
        return MagicMock(fetchone=lambda: None, fetchall=lambda: [])

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=fake_execute)
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.commit = AsyncMock()

    def fake_db():
        return fake_session

    from app.api.gst_billing import GSTInvoiceRequest, generate_gst_invoice
    from app.tenancy.context import PlanTier, TenantContext
    request = MagicMock()
    request.state.tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    request.app.state.db_session_factory = fake_db
    request.app.state.settings = None
    # Issuing is platform-only (see test_tenant_key_alone_cannot_issue_a_gst_invoice).
    monkeypatch.setenv("PLATFORM_ADMIN_KEY", _ADMIN_KEY)
    request.headers = {"x-admin-key": _ADMIN_KEY}
    result = await generate_gst_invoice(
        GSTInvoiceRequest(amount_inr=1180, buyer_name="Test Co", buyer_address="Mumbai"),
        request,
    )
    assert result["total_amount_inr"] == 1180
    assert "invoice_number" in result
    assert len(captured) > 0, "Invoice must be persisted to gst_invoices table"


# ── Issuing is platform-only, persisted under RLS, never a fake 201 ──────────

_ADMIN_KEY = "platform-admin-test-key"


class _RecordingSession:
    """Fake AsyncSession recording every statement, optionally failing the INSERT."""

    def __init__(self, fail_insert: bool = False) -> None:
        self.statements: list[tuple[str, dict]] = []
        self.committed = False
        self._fail_insert = fail_insert

    async def __aenter__(self) -> "_RecordingSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, query, params=None):
        from unittest.mock import MagicMock

        sql = str(query)
        self.statements.append((sql, dict(params or {})))
        if self._fail_insert and "INSERT INTO gst_invoices" in sql:
            raise RuntimeError("new row violates row-level security policy")
        return MagicMock(fetchall=lambda: [])

    async def commit(self) -> None:
        self.committed = True


def _gst_app(session: _RecordingSession | None):
    from fastapi import FastAPI

    from app.api.gst_billing import router
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

    ctx = TenantContext(
        tenant_id="t-gst", plan=PlanTier.STARTER, api_key_id="k-gst", roles=("admin",)
    )

    async def _resolve(key: str):
        return ctx if key == "ak_gst" else None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(router)
    app.state.db_session_factory = (lambda: session) if session is not None else None
    return app


_INVOICE_BODY = {"amount_inr": 1180, "buyer_name": "Acme Ltd", "buyer_address": "Mumbai"}


def test_tenant_key_alone_cannot_issue_a_gst_invoice(monkeypatch):
    """Regression: any tenant key (even a tenant admin) could mint a tax invoice
    carrying the platform's seller GSTIN for any amount/buyer it chose. Issuing
    is a platform billing action and now requires the platform admin key."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PLATFORM_ADMIN_KEY", _ADMIN_KEY)
    session = _RecordingSession()
    client = TestClient(_gst_app(session), raise_server_exceptions=False)

    resp = client.post("/billing/gst/invoice", json=_INVOICE_BODY,
                       headers={"X-API-Key": "ak_gst"})
    assert resp.status_code == 403, resp.text
    wrong = client.post("/billing/gst/invoice", json=_INVOICE_BODY,
                        headers={"X-API-Key": "ak_gst", "X-Admin-Key": "nope"})
    assert wrong.status_code == 403, wrong.text
    assert not any("INSERT" in sql for sql, _ in session.statements)


def test_gst_invoice_insert_runs_inside_the_tenant_rls_context(monkeypatch):
    """Regression: the INSERT ran with no app.tenant_id set, so under the table's
    FORCE RLS WITH CHECK policy it was rejected — and the handler swallowed that
    and still returned 201 with an invoice that was never retained."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PLATFORM_ADMIN_KEY", _ADMIN_KEY)
    session = _RecordingSession()
    client = TestClient(_gst_app(session), raise_server_exceptions=False)

    resp = client.post(
        "/billing/gst/invoice",
        json={**_INVOICE_BODY, "tenant_id": "t-billed"},
        headers={"X-API-Key": "ak_gst", "X-Admin-Key": _ADMIN_KEY},
    )
    assert resp.status_code == 201, resp.text

    sqls = [sql for sql, _ in session.statements]
    set_idx = next(i for i, sql in enumerate(sqls) if "set_config('app.tenant_id'" in sql)
    ins_idx = next(i for i, sql in enumerate(sqls) if "INSERT INTO gst_invoices" in sql)
    assert set_idx < ins_idx, sqls
    assert session.statements[set_idx][1]["tid"] == "t-billed"
    assert session.statements[ins_idx][1]["tid"] == "t-billed"
    assert session.committed


def test_gst_invoice_persist_failure_is_not_reported_as_created(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PLATFORM_ADMIN_KEY", _ADMIN_KEY)
    headers = {"X-API-Key": "ak_gst", "X-Admin-Key": _ADMIN_KEY}

    failing = TestClient(_gst_app(_RecordingSession(fail_insert=True)),
                         raise_server_exceptions=False)
    resp = failing.post("/billing/gst/invoice", json=_INVOICE_BODY, headers=headers)
    assert resp.status_code == 503, resp.text

    no_db = TestClient(_gst_app(None), raise_server_exceptions=False)
    resp = no_db.post("/billing/gst/invoice", json=_INVOICE_BODY, headers=headers)
    assert resp.status_code == 503, resp.text
