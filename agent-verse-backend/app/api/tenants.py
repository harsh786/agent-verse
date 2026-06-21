"""Tenant management endpoints: signup, profile, API-key CRUD."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from app.core.errors import ConflictError, NotFoundError, PlatformError
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/tenants", tags=["tenants"])


# ── utilities ─────────────────────────────────────────────────────────────────

def _hash_key(raw_key: str) -> str:
    """SHA-256 hex digest of a raw API key. The raw key is never stored."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _generate_raw_key(plan_prefix: str = "free") -> str:
    """Generate a cryptographically random API key with a recognisable prefix."""
    return f"av_{plan_prefix}_{secrets.token_urlsafe(32)}"


def _get_tenant_service(request: Request) -> Any:
    """Read the service from app.state (injected by create_app or tests)."""
    return request.app.state.tenant_service


def _require_tenant(request: Request) -> TenantContext:
    """FastAPI dependency — raises 401 if tenant middleware did not authenticate."""
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return ctx


# ── request / response models ─────────────────────────────────────────────────

class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr


class CreateKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("/signup", status_code=201)
async def signup(
    body: SignupRequest,
    request: Request,
) -> JSONResponse:
    """Create a new tenant account and return the initial API key."""
    svc = _get_tenant_service(request)
    try:
        result = await svc.create_tenant(name=body.name, email=str(body.email))
    except ConflictError as exc:
        return JSONResponse(exc.to_dict(), status_code=409)
    except PlatformError as exc:
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)
    return JSONResponse(result, status_code=201)


@router.get("/me")
async def get_me(
    request: Request,
    ctx: TenantContext = Depends(_require_tenant),
) -> JSONResponse:
    """Return the authenticated tenant's profile."""
    svc = _get_tenant_service(request)
    result = await svc.get_tenant(tenant_id=ctx.tenant_id)
    return JSONResponse(result)


@router.get("/me/keys")
async def list_keys(
    request: Request,
    ctx: TenantContext = Depends(_require_tenant),
) -> JSONResponse:
    """List all API keys for the current tenant. Raw keys are never returned here."""
    svc = _get_tenant_service(request)
    result = await svc.list_api_keys(tenant_id=ctx.tenant_id)
    return JSONResponse(result)


@router.post("/me/keys", status_code=201)
async def create_key(
    body: CreateKeyRequest,
    request: Request,
    ctx: TenantContext = Depends(_require_tenant),
) -> JSONResponse:
    """Create a new API key. The raw key is returned ONLY in this response."""
    svc = _get_tenant_service(request)
    result = await svc.create_api_key(
        tenant_id=ctx.tenant_id,
        name=body.name,
        scopes=body.scopes,
        expires_at=body.expires_at,
    )
    return JSONResponse(result, status_code=201)


@router.delete("/me/keys/{key_id}", status_code=204)
async def revoke_key(
    key_id: str,
    request: Request,
    ctx: TenantContext = Depends(_require_tenant),
) -> Response:
    """Revoke (deactivate) an API key owned by the current tenant."""
    svc = _get_tenant_service(request)
    try:
        await svc.revoke_api_key(tenant_id=ctx.tenant_id, key_id=key_id)
    except NotFoundError as exc:
        return JSONResponse(exc.to_dict(), status_code=404)
    return Response(status_code=204)
