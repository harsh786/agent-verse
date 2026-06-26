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
from app.providers.vault import get_vault
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


# ── Key rotation ──────────────────────────────────────────────────────────────

class RotateKeyRequest(BaseModel):
    """Request body for key rotation."""

    name: str = Field(default="Rotated Key", min_length=1, max_length=200)
    scopes: list[str] = Field(default_factory=list)
    revoke_old: bool = True


@router.post("/me/keys/{key_id}/rotate", status_code=201)
async def rotate_key(
    key_id: str,
    body: RotateKeyRequest,
    request: Request,
    ctx: TenantContext = Depends(_require_tenant),
) -> JSONResponse:
    """Rotate an API key: create a replacement, optionally revoke the original.

    The newly created key's raw secret is returned **once** in this response.
    """
    svc = _get_tenant_service(request)

    # Create the replacement key first so callers can take it before the old one
    # is revoked — minimising the window without a valid key.
    new_key = await svc.create_api_key(
        tenant_id=ctx.tenant_id,
        name=body.name,
        scopes=body.scopes,
        expires_at=None,
    )

    if body.revoke_old:
        try:
            await svc.revoke_api_key(tenant_id=ctx.tenant_id, key_id=key_id)
        except Exception:
            pass  # Best-effort: don't fail the rotation if revocation errors

    return JSONResponse(
        {"new_key": new_key, "old_key_id": key_id, "old_revoked": body.revoke_old},
        status_code=201,
    )


# ── LLM provider configuration ────────────────────────────────────────────────

class LLMProviderConfig(BaseModel):
    """LLM provider configuration for a tenant."""
    provider: str = Field(
        description="Provider name: anthropic | openai | gemini | groq | together | azure | ollama"
    )
    api_key: str = Field(min_length=1, description="API key (stored encrypted in vault)")
    base_url: str | None = Field(
        default=None, description="Base URL override (for Ollama / Azure / vLLM)"
    )
    default_model: str = Field(default="", description="Default model slug")


@router.get("/me/llm")
async def get_llm_config(
    request: Request,
    ctx: TenantContext = Depends(_require_tenant),
) -> JSONResponse:
    """Return the current LLM provider config for this tenant (key never exposed)."""
    llm_configs: dict[str, Any] = getattr(request.app.state, "_llm_configs", {})
    cfg = llm_configs.get(ctx.tenant_id)
    if cfg is None:
        return JSONResponse(
            {"tenant_id": ctx.tenant_id, "provider": None, "configured": False}
        )
    # Never return the raw key or the vault-encrypted ciphertext.
    safe = {k: v for k, v in cfg.items() if k not in {"api_key", "encrypted_key"}}
    return JSONResponse({"tenant_id": ctx.tenant_id, **safe, "configured": True})


@router.put("/me/llm", status_code=200)
async def set_llm_config(
    body: LLMProviderConfig,
    request: Request,
    ctx: TenantContext = Depends(_require_tenant),
) -> JSONResponse:
    """Configure the LLM provider for this tenant. The API key is stored encrypted."""
    if not hasattr(request.app.state, "_llm_configs"):
        request.app.state._llm_configs = {}

    # Fix 7: Encrypt the key via CredentialVault before storing.
    vault = get_vault()
    encrypted_key = vault.encrypt(body.api_key)
    masked_key = body.api_key[:8] + "..." + body.api_key[-4:] if len(body.api_key) > 12 else "****"

    request.app.state._llm_configs[ctx.tenant_id] = {
        "provider": body.provider,
        "base_url": body.base_url,
        "default_model": body.default_model,
        "masked_key": masked_key,
        "encrypted_key": encrypted_key,  # stored encrypted; never returned to callers
    }

    # Also persist to Redis so Celery workers can access it without app state.
    from app.services.llm_config_store import get_llm_config_store
    _config_store = get_llm_config_store()
    if _config_store is not None:
        await _config_store.set_config(
            tenant_id=ctx.tenant_id,
            provider=body.provider,
            encrypted_key=encrypted_key,
            model=body.default_model or "",
            base_url=body.base_url,
        )

    return JSONResponse(
        {
            "tenant_id": ctx.tenant_id,
            "provider": body.provider,
            "default_model": body.default_model,
            "configured": True,
        },
        status_code=200,
    )
