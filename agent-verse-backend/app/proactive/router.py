"""Proactive signals API (Phase 9 live wiring).

A trusted producer (a calendar/email integration, a scheduled sweep, a trigger)
posts a signal for the authenticated tenant; the wired ProactiveEngine decides —
under the per-principal consent/rate/quiet-hours gate — whether to reach out, and
delivers any message into the principal's chat thread (and origin channel).

Tenant is taken from the AUTHENTICATED context, never the body, so a signal can
only ever act within the caller's own tenant.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.proactive.signals import ProactiveSignal

router = APIRouter(prefix="/v1/proactive", tags=["proactive"])


class SignalRequest(BaseModel):
    kind: str = Field(..., description="signal kind, e.g. flight_delayed, inbound_email")
    principal_id: str | None = Field(
        None, description="who to reach; defaults to the tenant's default principal"
    )
    channel: str = Field("web", description="delivery channel")
    channel_user_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SignalResult(BaseModel):
    delivered: bool
    reason: str
    requires_confirmation: bool = False


def _tenant_id(request: Request) -> str:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx.tenant_id


@router.post("/signals", response_model=SignalResult, operation_id="proactive_ingest_signal")
async def ingest_signal(body: SignalRequest, request: Request) -> SignalResult:
    engine = getattr(request.app.state, "proactive_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="proactive engine not available")
    tenant_id = _tenant_id(request)
    signal = ProactiveSignal(
        kind=body.kind,
        tenant_id=tenant_id,
        principal_id=body.principal_id or tenant_id,
        channel=body.channel,
        payload={**body.payload, "_channel_user_id": body.channel_user_id}
        if body.channel_user_id
        else body.payload,
    )
    outcome = await engine.handle(signal)
    return SignalResult(
        delivered=outcome.delivered,
        reason=outcome.reason,
        requires_confirmation=outcome.requires_confirmation,
    )
