"""India DPDP (Digital Personal Data Protection Act 2023) endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/compliance/dpdp", tags=["compliance"])


class ConsentRequest(BaseModel):
    data_principal_id: str
    purpose: str
    consent_given: bool


class ErasureRequest(BaseModel):
    data_principal_id: str
    reason: str = ""


def _req_tenant(r: Request) -> Any:
    ctx = getattr(r.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


@router.post("/consent", status_code=201)
async def record_consent(body: ConsentRequest, request: Request) -> dict[str, Any]:
    """Record consent for a data principal."""
    tenant = _req_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if not db:
        raise HTTPException(503, "Database unavailable")
    consent_id = uuid.uuid4().hex
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
        await session.execute(
            text("""INSERT INTO dpdp_consents
                    (id, tenant_id, data_principal_id, purpose, consent_given)
                    VALUES (:id, :tid, :dpid, :purpose, :given)"""),
            {
                "id": consent_id,
                "tid": tenant.tenant_id,
                "dpid": body.data_principal_id,
                "purpose": body.purpose,
                "given": body.consent_given,
            },
        )
        await session.commit()
    return {"consent_id": consent_id, "status": "recorded", "consent_given": body.consent_given}


@router.get("/consent/{data_principal_id}")
async def get_consents(data_principal_id: str, request: Request) -> list[dict[str, Any]]:
    tenant = _req_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if not db:
        return []
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
        rows = (
            await session.execute(
                text(
                    "SELECT id, purpose, consent_given, consent_timestamp FROM dpdp_consents WHERE tenant_id = :tid AND data_principal_id = :dpid ORDER BY consent_timestamp DESC"
                ),
                {"tid": tenant.tenant_id, "dpid": data_principal_id},
            )
        ).fetchall()
    return [
        {"id": r[0], "purpose": r[1], "consent_given": r[2], "timestamp": str(r[3])} for r in rows
    ]


@router.post("/erasure-request", status_code=202)
async def request_erasure(body: ErasureRequest, request: Request) -> dict[str, Any]:
    """Data principal requests erasure (DPDP Article 12)."""
    tenant = _req_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if not db:
        raise HTTPException(503, "Database unavailable")
    req_id = uuid.uuid4().hex
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
        await session.execute(
            text(
                "INSERT INTO dpdp_erasure_requests (id, tenant_id, data_principal_id, status) VALUES (:id, :tid, :dpid, 'pending')"
            ),
            {"id": req_id, "tid": tenant.tenant_id, "dpid": body.data_principal_id},
        )
        await session.commit()
    return {
        "request_id": req_id,
        "status": "accepted",
        "message": "Erasure request recorded. Personal data will be deleted within 30 days via our automated erasure pipeline.",
        "grievance_officer": "dpo@agentverse.ai",
    }


@router.get("/grievance-officer")
async def grievance_officer_contact() -> dict[str, Any]:
    """Grievance officer contact — required by DPDP Act."""
    return {
        "name": "Data Protection Officer",
        "email": "dpo@agentverse.ai",
        "response_time": "30 days",
        "act": "Digital Personal Data Protection Act 2023 (India)",
        "rights": ["access", "correction", "erasure", "grievance_redressal", "nomination"],
    }
