"""GST-compliant billing engine for Indian customers.

Generates valid tax invoices with GSTIN, HSN/SAC codes, IGST/CGST/SGST breakdown.
Required by India GST Act to legally invoice Indian customers.
"""
from __future__ import annotations
import uuid
from datetime import datetime, UTC
from typing import Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/billing/gst", tags=["billing"])

# HSN code for SaaS / IT services under GST
_SAC_CODE_SAAS = "998314"  # IT services — Data processing, hosting, related activities
_GST_RATE = 0.18           # 18% GST for IT services


class GSTInvoiceRequest(BaseModel):
    tenant_id_override: str | None = None   # for admin use
    plan: str = "starter"
    amount_inr: float
    buyer_gstin: str | None = None          # if B2B, buyer's GSTIN
    buyer_name: str
    buyer_address: str
    buyer_state_code: str = "27"            # Maharashtra default
    billing_month: str = ""                 # YYYY-MM format


class GSTInvoice(BaseModel):
    invoice_number: str
    invoice_date: str
    seller_gstin: str
    seller_name: str = "AgentVerse Technologies Pvt Ltd"
    buyer_name: str
    buyer_gstin: str | None
    buyer_address: str
    sac_code: str = _SAC_CODE_SAAS
    taxable_amount_inr: float
    igst_amount: float = 0.0               # for inter-state
    cgst_amount: float = 0.0              # for intra-state
    sgst_amount: float = 0.0              # for intra-state
    total_gst_amount: float
    total_amount_inr: float
    is_inter_state: bool
    description: str


def _generate_invoice_number(tenant_prefix: str) -> str:
    now = datetime.now(UTC)
    return f"AV/{now.year}-{str(now.year + 1)[-2:]}/{tenant_prefix[:4].upper()}/{uuid.uuid4().hex[:6].upper()}"


def _is_inter_state(seller_state: str, buyer_state: str) -> bool:
    return seller_state != buyer_state


@router.post("/invoice", status_code=201)
async def generate_gst_invoice(body: GSTInvoiceRequest, request: Request) -> GSTInvoice:
    """Generate a GST-compliant tax invoice."""
    from app.core.config import get_settings
    s = get_settings()
    seller_gstin = getattr(s, "seller_gstin", "27AAAAA0000A1Z5")  # placeholder
    seller_state = seller_gstin[0:2] if seller_gstin else "27"     # state code from GSTIN
    inter_state = _is_inter_state(seller_state, body.buyer_state_code)

    taxable = round(body.amount_inr / 1.18, 2)  # back-calculate from GST-inclusive
    gst_amount = round(body.amount_inr - taxable, 2)

    igst = cgst = sgst = 0.0
    if inter_state:
        igst = gst_amount
    else:
        cgst = round(gst_amount / 2, 2)
        sgst = round(gst_amount / 2, 2)

    tenant = getattr(request.state, "tenant", None)
    prefix = (body.tenant_id_override or (tenant.tenant_id[:6] if tenant else "TENANT"))

    return GSTInvoice(
        invoice_number=_generate_invoice_number(prefix),
        invoice_date=datetime.now(UTC).strftime("%d/%m/%Y"),
        seller_gstin=seller_gstin,
        buyer_name=body.buyer_name,
        buyer_gstin=body.buyer_gstin,
        buyer_address=body.buyer_address,
        taxable_amount_inr=taxable,
        igst_amount=igst,
        cgst_amount=cgst,
        sgst_amount=sgst,
        total_gst_amount=gst_amount,
        total_amount_inr=body.amount_inr,
        is_inter_state=inter_state,
        description=f"AgentVerse {body.plan.title()} Plan — {body.billing_month or datetime.now(UTC).strftime('%B %Y')}",
    )


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
