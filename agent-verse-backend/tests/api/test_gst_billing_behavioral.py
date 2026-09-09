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
async def test_gst_invoice_persisted_to_db():
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
    result = await generate_gst_invoice(
        GSTInvoiceRequest(amount_inr=1180, buyer_name="Test Co", buyer_address="Mumbai"),
        request,
    )
    assert result["total_amount_inr"] == 1180
    assert "invoice_number" in result
    assert len(captured) > 0, "Invoice must be persisted to gst_invoices table"
