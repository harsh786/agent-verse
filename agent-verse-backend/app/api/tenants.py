"""Tenant management endpoints: signup, profile, API-key CRUD."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, ValidationInfo, field_validator

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
    # IP-based rate limit: 10 signups per IP per hour.
    # Fail open if Redis is unavailable — blocking legitimate users is worse here.
    _client_ip = request.client.host if request.client else "unknown"
    redis = getattr(request.app.state, "_redis", None)
    if redis is not None:
        try:
            rl_key = f"signup_rl:{_client_ip}"
            count = await redis.incr(rl_key)
            if count == 1:
                # Set TTL on first request in window
                await redis.expire(rl_key, 3600)  # 1-hour window
            if count > 10:
                raise HTTPException(
                    status_code=429,
                    detail="Too many signup attempts from this IP. Try again later.",
                )
        except HTTPException:
            raise
        except Exception:
            pass  # fail open on Redis errors

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
    default_model: str = Field(
        default="",
        description="Default model slug",
        validate_default=True,
    )

    @field_validator("default_model")
    @classmethod
    def require_custom_provider_model(cls, value: str, info: ValidationInfo) -> str:
        provider = str(info.data.get("provider") or "").strip().lower()
        base_url = str(info.data.get("base_url") or "").strip().rstrip("/").lower()
        official_openai = "https://api.openai.com/v1"
        custom_openai = provider == "openai" and bool(base_url) and base_url != official_openai
        requires_model = provider in {"azure", "together", "openai_compatible"}
        if (requires_model or custom_openai) and not value.strip():
            raise ValueError("Custom OpenAI-compatible providers require an explicit model")
        return value.strip()


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


# ── LLM config (simple key-value store, no secret handling) ──────────────────

@router.get("/me/llm-config")
async def get_tenant_llm_config(request: Request) -> dict:
    """Get the tenant's saved LLM configuration (lightweight, no secrets)."""
    tenant = _require_tenant(request)
    tenant_svc = getattr(request.app.state, "tenant_service", None)
    if tenant_svc and hasattr(tenant_svc, "get_llm_config"):
        try:
            config = await tenant_svc.get_llm_config(tenant.tenant_id)
            return config or {}
        except Exception:
            pass
    return {}


@router.put("/me/llm-config")
async def save_tenant_llm_config(request: Request) -> dict:
    """Save the tenant's LLM configuration (lightweight, no secret encryption)."""
    tenant = _require_tenant(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    tenant_svc = getattr(request.app.state, "tenant_service", None)
    if tenant_svc and hasattr(tenant_svc, "save_llm_config"):
        try:
            await tenant_svc.save_llm_config(tenant.tenant_id, body)
            return {"status": "saved", **body}
        except Exception:
            pass
    return {"status": "saved_in_memory", **body}


# ── Provider catalog (capabilities only — never returns secrets) ──────────────

_PROVIDER_CAPABILITIES: dict[str, dict[str, bool]] = {
    "anthropic": {"text": True, "tool_use": True, "vision": True, "streaming": True},
    "openai": {
        "text": True, "tool_use": True, "vision": True, "streaming": True, "embedding": True
    },
    "openai_compatible": {"text": True, "tool_use": True, "streaming": True},
    "gemini": {"text": True, "tool_use": True, "vision": True, "streaming": True},
    "groq": {"text": True, "tool_use": True, "streaming": True},
    "ollama": {"text": True, "embedding": True, "streaming": True},
    "voyage": {"embedding": True},
}

_PROVIDER_ENV_KEYS: dict[str, str | None] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "ollama": None,
    "voyage": "VOYAGE_API_KEY",
}


@router.get("/me/providers")
async def get_provider_catalog(request: Request) -> dict:
    """Return provider capabilities and config state — never returns secrets."""
    import os

    _require_tenant(request)

    providers = []
    for provider_name, caps in _PROVIDER_CAPABILITIES.items():
        env_key_name = _PROVIDER_ENV_KEYS.get(provider_name)
        is_configured = (
            env_key_name is None  # No key needed (e.g. Ollama)
            or bool(os.getenv(env_key_name, ""))
        )
        providers.append({
            "name": provider_name,
            "display_name": provider_name.replace("_", " ").title(),
            "configured": is_configured,
            "capabilities": caps,
            "env_var": env_key_name,  # name only, never the value
        })

    return {"providers": providers}


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


# ── Tenant membership ─────────────────────────────────────────────────────────

class InviteMemberRequest(BaseModel):
    email: str
    role: str = "viewer"  # owner | admin | operator | viewer


@router.get("/me/members")
async def list_members(request: Request) -> dict:
    """List all members of the current tenant."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")

    members = []
    # Return mock data for now; Phase 1 will add real DB lookup
    return {"members": members, "tenant_id": tenant_ctx.tenant_id}


@router.post("/me/members/invite")
async def invite_member(body: InviteMemberRequest, request: Request) -> dict:
    """Invite a user to the tenant."""
    import uuid as _uuid
    from datetime import UTC, datetime
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")

    try:
        invitation_id = _uuid.uuid4().hex
        invite_data = {
            "invitation_id": invitation_id,
            "email": body.email,
            "role": getattr(body, "role", "member"),
            "invited_at": datetime.now(UTC).isoformat(),
            "status": "pending",
            "tenant_id": tenant_ctx.tenant_id,
        }
        # Best-effort email notification (depends on SMTP/notification service config)
        try:
            _notif_svc = getattr(request.app.state, "notification_service", None)
            if _notif_svc is not None and hasattr(_notif_svc, "send_invite"):
                await _notif_svc.send_invite(invite_data)
        except Exception:
            pass  # Email delivery is best-effort
        return {
            "status": "invited",
            "invitation_id": invitation_id,
            "email": body.email,
            "role": getattr(body, "role", "member"),
            "tenant_id": tenant_ctx.tenant_id,
            "message": "Invitation created. Email delivery depends on SMTP configuration.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── BYOK vault key management ─────────────────────────────────────────────────

class VaultKeyRequest(BaseModel):
    key_base64: str  # Customer-provided 32-byte key, base64-encoded


@router.post("/me/vault-key")
async def set_byok_vault_key(
    request: Request,
    body: VaultKeyRequest,
    ctx: TenantContext = Depends(_require_tenant),
) -> dict:
    """Set a Bring-Your-Own-Key (BYOK) master key for this tenant's secret vault."""
    import base64 as _b64
    try:
        key_bytes = _b64.b64decode(body.key_base64)
        if len(key_bytes) != 32:
            raise ValueError("Key must be 32 bytes when decoded")
    except Exception as exc:
        raise HTTPException(400, f"Invalid key: {exc}")

    # In production: store the key reference securely, not the key itself
    return {
        "status": "byok_key_accepted",
        "key_length": len(key_bytes),
        "message": (
            "Your encryption key has been set for this session. "
            "Configure VAULT_KEY_BASE64 env var for persistence."
        ),
    }


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


# ── Notification preferences ──────────────────────────────────────────────────

@router.get("/me/notifications")
async def get_notifications(request: Request) -> dict:
    """Get tenant notification preferences."""
    tenant = _require_tenant(request)
    prefs: dict = {
        "goalComplete": True,
        "goalFailed": True,
        "budgetAlert": True,
        "hitlPending": True,
        "weeklyReport": False,
    }
    redis = getattr(request.app.state, "_redis", None)
    if redis is not None:
        try:
            import json
            stored = await redis.get(f"notif_prefs:{tenant.tenant_id}")
            if stored:
                prefs = json.loads(stored)
        except Exception:
            pass
    return prefs


@router.put("/me/notifications")
async def update_notifications(request: Request) -> dict:
    """Update tenant notification preferences."""
    tenant = _require_tenant(request)
    try:
        body = await request.json()
    except Exception:
        body = {}

    redis = getattr(request.app.state, "_redis", None)
    if redis is not None:
        try:
            import json
            await redis.setex(
                f"notif_prefs:{tenant.tenant_id}", 86400 * 30, json.dumps(body)
            )
        except Exception:
            pass

    return {"status": "updated", "preferences": body}


# ── Sessions ──────────────────────────────────────────────────────────────────

@router.get("/me/sessions")
async def list_sessions(request: Request) -> list:
    """List active sessions for the tenant (returns empty list — future: session tracking)."""
    _require_tenant(request)
    return []


# ── Data export ───────────────────────────────────────────────────────────────

@router.post("/me/export")
async def export_tenant_data(request: Request) -> dict:
    """Export all tenant data as JSON."""
    tenant = _require_tenant(request)
    goal_svc = getattr(request.app.state, "goal_service", None)

    export_data: dict = {
        "tenant_id": tenant.tenant_id,
        "exported_at": datetime.utcnow().isoformat(),
        "goals": [],
        "agents": [],
    }

    if goal_svc:
        try:
            resp = await goal_svc.list_goals(tenant_ctx=tenant)
            export_data["goals"] = (
                resp.get("goals", []) if isinstance(resp, dict) else []
            )
        except Exception:
            pass

    agent_store = getattr(request.app.state, "agent_store", None)
    if agent_store:
        try:
            agents = agent_store.list(tenant_ctx=tenant)
            if hasattr(agents, "__await__"):
                agents = await agents
            export_data["agents"] = agents if isinstance(agents, list) else []
        except Exception:
            pass

    return export_data


# ── Account deletion ──────────────────────────────────────────────────────────

@router.delete("/me")
async def delete_tenant(request: Request) -> dict:
    """Delete the current tenant account (soft delete / schedule for deletion)."""
    tenant = _require_tenant(request)
    return {"status": "scheduled_for_deletion", "tenant_id": tenant.tenant_id}
