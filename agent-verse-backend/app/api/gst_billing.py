"""GST-compliant billing engine for India. Persists invoices for 7-year retention."""

from __future__ import annotations

import hmac
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing/gst", tags=["billing"])

_SAC_CODE_SAAS = "998314"
_GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


class GSTInvoiceRequest(BaseModel):
    amount_inr: Annotated[
        float, Field(gt=0, description="Invoice amount in INR (must be positive)")
    ]
    buyer_name: str = Field(min_length=1)
    buyer_address: str = Field(min_length=1)
    buyer_state_code: str = "27"
    buyer_gstin: str | None = None
    plan: str = "starter"
    billing_month: str = ""
    # Tenant being invoiced (platform operator call); defaults to the caller's tenant.
    tenant_id: str | None = None

    @field_validator("buyer_gstin")
    @classmethod
    def validate_gstin(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _GSTIN_PATTERN.match(v.upper()):
            raise ValueError(
                f"Invalid GSTIN format: '{v}'. "
                "GSTIN must be 15 chars: 2-digit state code + PAN + entity + checksum + Z + check"
            )
        return v.upper()


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Authentication required")
    return ctx


def _require_platform_billing(request: Request) -> None:
    """Only the platform (seller) may issue a tax invoice.

    A GST invoice is issued BY AgentVerse (it carries the platform's seller
    GSTIN) TO a tenant. This used to accept any tenant API key, so a tenant
    could mint "official" invoices for any amount and buyer it liked. Issuing
    now requires the platform admin key (``PLATFORM_ADMIN_KEY`` via
    ``X-Admin-Key``), matching app/api/admin.py. 503 when unconfigured.
    """
    admin_key = os.getenv("PLATFORM_ADMIN_KEY", "")
    if not admin_key:
        raise HTTPException(503, "Platform admin not configured; GST invoicing disabled")
    presented = request.headers.get("x-admin-key", "") or ""
    if not presented or not hmac.compare_digest(presented.encode(), admin_key.encode()):
        raise HTTPException(403, "Platform billing privileges required to issue GST invoices")


def _generate_invoice_number(tenant_prefix: str) -> str:
    now = datetime.now(UTC)
    return f"AV/{now.year}-{str(now.year + 1)[-2:]}/{tenant_prefix[:4].upper()}/{uuid.uuid4().hex[:6].upper()}"  # noqa: E501


@router.post("/invoice", status_code=201)
async def generate_gst_invoice(body: GSTInvoiceRequest, request: Request) -> dict[str, Any]:
    """Generate and persist a GST-compliant tax invoice."""
    from app.core.config import get_settings

    caller = _require_tenant(request)
    _require_platform_billing(request)
    billed_tenant_id = body.tenant_id or caller.tenant_id
    s = get_settings()
    seller_gstin = getattr(s, "seller_gstin", "27AAAAA0000A1Z5")
    seller_name = getattr(s, "seller_name", "AgentVerse Technologies Pvt Ltd")
    seller_state = seller_gstin[:2]
    inter_state = seller_state != body.buyer_state_code[:2]

    taxable = round(body.amount_inr / 1.18, 2)
    gst_total = round(body.amount_inr - taxable, 2)
    igst = gst_total if inter_state else 0.0
    cgst = round(gst_total / 2, 2) if not inter_state else 0.0
    sgst = round(gst_total / 2, 2) if not inter_state else 0.0

    invoice_number = _generate_invoice_number(billed_tenant_id)
    invoice_date = datetime.now(UTC).strftime("%d/%m/%Y")

    invoice = {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "seller_gstin": seller_gstin,
        "seller_name": seller_name,
        "buyer_name": body.buyer_name,
        "buyer_gstin": body.buyer_gstin,
        "buyer_address": body.buyer_address,
        "sac_code": _SAC_CODE_SAAS,
        "taxable_amount_inr": taxable,
        "igst_amount": igst,
        "cgst_amount": cgst,
        "sgst_amount": sgst,
        "total_gst_amount": gst_total,
        "total_amount_inr": body.amount_inr,
        "is_inter_state": inter_state,
        "description": f"AgentVerse {body.plan.title()} Plan — {body.billing_month or datetime.now(UTC).strftime('%B %Y')}",  # noqa: E501
    }

    # Persist to DB for 7-year GST retention compliance. Two old bugs here: the
    # INSERT ran without the tenant RLS context, so under gst_invoices' FORCE RLS
    # ``WITH CHECK`` policy it was rejected; and that error was swallowed and the
    # handler still returned 201 with an invoice that was never retained. An
    # invoice that is not stored has not been issued -> 503.
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(503, "Invoice store unavailable; invoice not issued")
    try:
        import json as _json

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, sqlalchemy_rls_context(session, billed_tenant_id):
            await session.execute(
                text(
                    "INSERT INTO gst_invoices (id, tenant_id, invoice_number, invoice_date, "
                    "buyer_name, buyer_gstin, taxable_amount_inr, total_gst_amount, "
                    "total_amount_inr, invoice_json) VALUES "
                    "(:id, :tid, :inv_num, :inv_date, :buyer, :gstin, :taxable, :gst, :total, CAST(:json AS json))"  # noqa: E501
                ),
                {
                    "id": uuid.uuid4().hex,
                    "tid": billed_tenant_id,
                    "inv_num": invoice_number,
                    "inv_date": invoice_date,
                    "buyer": body.buyer_name,
                    "gstin": body.buyer_gstin,
                    "taxable": taxable,
                    "gst": gst_total,
                    "total": body.amount_inr,
                    "json": _json.dumps(invoice),
                },
            )
            await session.commit()
    except Exception as exc:
        logger.warning("gst_invoice_persist_failed: %s", exc)
        raise HTTPException(503, "Invoice could not be persisted; invoice not issued") from exc

    return invoice


@router.get("/invoices")
async def list_invoices(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    """List persisted GST invoices for this tenant."""
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if not db:
        return []
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
        rows = (
            await session.execute(
                text(
                    "SELECT invoice_number, invoice_date, buyer_name, total_amount_inr, created_at "
                    "FROM gst_invoices WHERE tenant_id = :tid ORDER BY created_at DESC LIMIT :lim"
                ),
                {"tid": tenant.tenant_id, "lim": limit},
            )
        ).fetchall()
    return [
        {
            "invoice_number": r[0],
            "invoice_date": r[1],
            "buyer_name": r[2],
            "total_amount_inr": float(r[3] or 0),
            "created_at": str(r[4]),
        }
        for r in rows
    ]


@router.get("/hsn-lookup/{sac_code}")
async def hsn_lookup(sac_code: str) -> dict[str, Any]:
    """Look up HSN/SAC code description and GST rate."""
    known = {
        "998314": {"description": "IT services — Data processing, hosting", "gst_rate": 18},
        "998313": {"description": "Software development services", "gst_rate": 18},
        "997331": {"description": "Software downloads / SaaS", "gst_rate": 18},
    }
    if sac_code not in known:
        raise HTTPException(404, f"SAC code {sac_code} not in lookup table")
    return {"sac_code": sac_code, **known[sac_code]}
