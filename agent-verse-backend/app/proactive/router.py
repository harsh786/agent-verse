"""Proactive signals + opt-in preferences API (Phase 9 live wiring).

A trusted producer (a calendar/email integration, a scheduled sweep, a trigger)
posts a signal for the authenticated tenant; the wired ProactiveEngine decides —
under the per-principal opt-in / consent / quiet-hours / daily-cap gate — whether
to reach out, and delivers any message into the principal's chat thread (and
origin channel).

Outreach is opt-in (a10-F227-02): a principal is contacted only after an
operator stored its preferences with ``PUT /v1/proactive/preferences/{id}``;
``DELETE`` opts it out again. Posting signals and changing preferences need the
``operator`` role; reading preferences needs ``viewer``.

Tenant is taken from the AUTHENTICATED context, never the body, so a signal or a
preference can only ever act within the caller's own tenant.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator

from app.chat.proactive import ProactivePreferences
from app.proactive.signals import ProactiveSignal
from app.tenancy.rbac import require_role

router = APIRouter(prefix="/v1/proactive", tags=["proactive"])

_CHANNEL_NAME = r"^[a-z0-9_]{1,32}$"
# Hard ceiling on the per-principal daily cap: proactive outreach must never be
# a firehose, whatever an operator stores.
MAX_PER_DAY_CEILING = 20
_PrincipalId = Path(..., min_length=1, max_length=200)


class SignalRequest(BaseModel):
    kind: str = Field(..., description="signal kind, e.g. flight_delayed, inbound_email")
    principal_id: str | None = Field(
        None,
        max_length=200,
        description="who to reach; defaults to the tenant's default principal",
    )
    channel: str = Field("web", description="delivery channel")
    channel_user_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SignalResult(BaseModel):
    delivered: bool
    reason: str
    requires_confirmation: bool = False
    channel_delivered: bool | None = Field(
        None,
        description=(
            "null when only the web thread was targeted; true/false whether the push "
            "to the signal's external channel succeeded (false also when no channel "
            "push is configured — the message is still in the principal's thread)"
        ),
    )


def _tenant_id(request: Request) -> str:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx.tenant_id


@router.post(
    "/signals",
    response_model=SignalResult,
    operation_id="proactive_ingest_signal",
    dependencies=[Depends(require_role("operator"))],
)
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
        channel_delivered=outcome.channel_delivered,
    )


# ── opt-in preferences ───────────────────────────────────────────────────────


class QuietHours(BaseModel):
    start: int = Field(..., ge=0, le=23, description="local hour quiet hours begin")
    end: int = Field(..., ge=0, le=23, description="local hour they end (may wrap midnight)")


class PreferencesBody(BaseModel):
    enabled: bool = Field(..., description="false keeps the record but sends nothing")
    channels: list[str] = Field(
        default_factory=lambda: ["web"], min_length=1, max_length=10
    )
    quiet_hours: QuietHours | None = None
    timezone: str = Field("UTC", max_length=64, description="IANA zone, e.g. Europe/Berlin")
    max_per_day: int = Field(3, ge=1, le=MAX_PER_DAY_CEILING)

    @field_validator("channels")
    @classmethod
    def _channels(cls, value: list[str]) -> list[str]:
        import re

        for ch in value:
            if not re.match(_CHANNEL_NAME, ch):
                raise ValueError(f"invalid channel name {ch!r}")
        return sorted(set(value))

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _quiet_window(self) -> PreferencesBody:
        if self.quiet_hours is not None and self.quiet_hours.start == self.quiet_hours.end:
            raise ValueError("quiet_hours start and end must differ")
        return self


class PreferencesView(BaseModel):
    principal_id: str
    opted_in: bool
    enabled: bool = False
    channels: list[str] = Field(default_factory=list)
    quiet_hours: QuietHours | None = None
    timezone: str = "UTC"
    max_per_day: int = 0


def _store(request: Request) -> Any:
    store = getattr(request.app.state, "proactive_preferences", None)
    if store is None:
        raise HTTPException(status_code=503, detail="proactive preferences not available")
    return store


def _view(principal_id: str, prefs: ProactivePreferences | None) -> PreferencesView:
    if prefs is None:
        return PreferencesView(principal_id=principal_id, opted_in=False)
    quiet = prefs.quiet_hours
    return PreferencesView(
        principal_id=principal_id,
        opted_in=True,
        enabled=prefs.enabled,
        channels=sorted(prefs.channels),
        quiet_hours=QuietHours(start=quiet[0], end=quiet[1]) if quiet else None,
        timezone=prefs.timezone,
        max_per_day=prefs.max_per_day,
    )


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=f"proactive preferences store unavailable ({type(exc).__name__})",
    )


@router.get(
    "/preferences/{principal_id}",
    response_model=PreferencesView,
    operation_id="proactive_get_preferences",
    dependencies=[Depends(require_role("viewer"))],
)
async def get_preferences(
    request: Request, principal_id: str = _PrincipalId
) -> PreferencesView:
    tenant_id = _tenant_id(request)
    try:
        prefs = await _store(request).get(tenant_id, principal_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _unavailable(exc) from exc
    return _view(principal_id, prefs)


@router.put(
    "/preferences/{principal_id}",
    response_model=PreferencesView,
    operation_id="proactive_put_preferences",
    dependencies=[Depends(require_role("operator"))],
)
async def put_preferences(
    body: PreferencesBody, request: Request, principal_id: str = _PrincipalId
) -> PreferencesView:
    """Opt a principal in (or update its consent controls)."""
    tenant_id = _tenant_id(request)
    prefs = ProactivePreferences(
        enabled=body.enabled,
        quiet_hours=(body.quiet_hours.start, body.quiet_hours.end) if body.quiet_hours else None,
        max_per_day=body.max_per_day,
        channels=frozenset(body.channels),
        timezone=body.timezone,
    )
    ctx = request.state.tenant
    actor = getattr(ctx, "user_id", None) or getattr(ctx, "api_key_id", None)
    try:
        await _store(request).put(
            tenant_id, principal_id, prefs, updated_by=str(actor) if actor else None
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _unavailable(exc) from exc
    return _view(principal_id, prefs)


@router.delete(
    "/preferences/{principal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="proactive_delete_preferences",
    dependencies=[Depends(require_role("operator"))],
)
async def delete_preferences(request: Request, principal_id: str = _PrincipalId) -> Response:
    """Opt a principal out entirely (idempotent)."""
    tenant_id = _tenant_id(request)
    try:
        await _store(request).delete(tenant_id, principal_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _unavailable(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
