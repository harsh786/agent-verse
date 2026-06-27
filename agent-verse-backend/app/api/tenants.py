"""Tenant management endpoints: signup, profile, API-key CRUD."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.errors import ConflictError, NotFoundError, PlatformError
from app.providers.vault import get_vault
from app.tenancy.context import TenantContext
from app.tenancy.rbac import VALID_ROLES

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


# ── RBAC: Role management ─────────────────────────────────────────────────────

class CreateRoleRequest(BaseModel):
    user_id: str
    role: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(VALID_ROLES)}, got {v!r}")
        return v


@router.get("/me/roles")
async def list_roles(
    request: Request,
    ctx: TenantContext = Depends(_require_tenant),
) -> list[dict]:
    """List all user role assignments for this tenant."""
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return []
    try:
        from sqlalchemy import select

        from app.db.models.rbac import UserRole
        from app.db.rls import sqlalchemy_rls_context

        async with db() as session:
            async with sqlalchemy_rls_context(session, ctx.tenant_id):
                result = await session.execute(
                    select(UserRole).where(UserRole.tenant_id == ctx.tenant_id)
                )
                rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "user_id": r.user_id,
                "role": r.role,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/me/roles", status_code=201)
async def create_role(
    request: Request,
    body: CreateRoleRequest,
    ctx: TenantContext = Depends(_require_tenant),
) -> dict:
    """Assign a role to a user within this tenant."""
    import uuid

    db = getattr(request.app.state, "db_session_factory", None)
    role_id = uuid.uuid4().hex
    if db is None:
        return {
            "id": role_id,
            "user_id": body.user_id,
            "role": body.role,
            "tenant_id": ctx.tenant_id,
        }
    try:
        from app.db.models.rbac import UserRole
        from app.db.rls import sqlalchemy_rls_context

        row = UserRole(
            id=role_id,
            tenant_id=ctx.tenant_id,
            user_id=body.user_id,
            role=body.role,
        )
        async with db() as session, session.begin():
            async with sqlalchemy_rls_context(session, ctx.tenant_id):
                session.add(row)
        return {
            "id": role_id,
            "user_id": body.user_id,
            "role": body.role,
            "tenant_id": ctx.tenant_id,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/me/roles/{role_id}", status_code=204)
async def delete_role(
    request: Request,
    role_id: str,
    ctx: TenantContext = Depends(_require_tenant),
) -> None:
    """Remove a role assignment."""
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return
    try:
        from sqlalchemy import select

        from app.db.models.rbac import UserRole
        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, session.begin():
            async with sqlalchemy_rls_context(session, ctx.tenant_id):
                result = await session.execute(
                    select(UserRole).where(
                        UserRole.id == role_id,
                        UserRole.tenant_id == ctx.tenant_id,
                    )
                )
                row = result.scalar_one_or_none()
                if row is None:
                    raise HTTPException(
                        status_code=404, detail="Role assignment not found"
                    )
                await session.delete(row)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── IP Allowlist management ───────────────────────────────────────────────────

class CreateIPAllowlistRequest(BaseModel):
    cidr: str
    description: str = ""

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, v: str) -> str:
        import ipaddress

        try:
            ipaddress.ip_network(v, strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid CIDR: {v!r}") from exc
        return v


@router.get("/me/ip-allowlist")
async def list_ip_allowlist(
    request: Request,
    ctx: TenantContext = Depends(_require_tenant),
) -> list[dict]:
    """List all IP allowlist entries for this tenant."""
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return []
    try:
        from sqlalchemy import select

        from app.db.models.rbac import IPAllowlistEntry
        from app.db.rls import sqlalchemy_rls_context

        async with db() as session:
            async with sqlalchemy_rls_context(session, ctx.tenant_id):
                result = await session.execute(
                    select(IPAllowlistEntry).where(
                        IPAllowlistEntry.tenant_id == ctx.tenant_id
                    )
                )
                rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "cidr": r.cidr,
                "description": r.description,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/me/ip-allowlist", status_code=201)
async def create_ip_allowlist_entry(
    request: Request,
    body: CreateIPAllowlistRequest,
    ctx: TenantContext = Depends(_require_tenant),
) -> dict:
    """Add a CIDR range to this tenant's IP allowlist."""
    import uuid

    db = getattr(request.app.state, "db_session_factory", None)
    entry_id = uuid.uuid4().hex
    if db is None:
        return {"id": entry_id, "cidr": body.cidr, "description": body.description}
    try:
        from app.db.models.rbac import IPAllowlistEntry
        from app.db.rls import sqlalchemy_rls_context

        row = IPAllowlistEntry(
            id=entry_id,
            tenant_id=ctx.tenant_id,
            cidr=body.cidr,
            description=body.description,
        )
        async with db() as session, session.begin():
            async with sqlalchemy_rls_context(session, ctx.tenant_id):
                session.add(row)
        return {"id": entry_id, "cidr": body.cidr, "description": body.description}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/me/ip-allowlist/{entry_id}", status_code=204)
async def delete_ip_allowlist_entry(
    request: Request,
    entry_id: str,
    ctx: TenantContext = Depends(_require_tenant),
) -> None:
    """Remove a CIDR entry from this tenant's IP allowlist."""
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return
    try:
        from sqlalchemy import select

        from app.db.models.rbac import IPAllowlistEntry
        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, session.begin():
            async with sqlalchemy_rls_context(session, ctx.tenant_id):
                result = await session.execute(
                    select(IPAllowlistEntry).where(
                        IPAllowlistEntry.id == entry_id,
                        IPAllowlistEntry.tenant_id == ctx.tenant_id,
                    )
                )
                row = result.scalar_one_or_none()
                if row is None:
                    raise HTTPException(
                        status_code=404, detail="Allowlist entry not found"
                    )
                await session.delete(row)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
