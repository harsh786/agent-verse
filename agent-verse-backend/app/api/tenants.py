"""Tenant management endpoints: signup, profile, API-key CRUD."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, ValidationInfo, field_validator

from app.core.errors import ConflictError, NotFoundError, PlatformError
from app.tenancy.context import TenantContext
from app.tenancy.rbac import VALID_ROLES, require_role

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
    from app.api._deps import get_tenant_service as _get_ts

    return _get_ts(request)


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
    # 10 signups per client IP per hour (trusted-proxy aware IP; Redis across
    # replicas, in-process window without Redis / on a Redis error). It keyed
    # on request.client.host — the load balancer behind a proxy — and failed
    # open on Redis errors.
    from app.tenancy.ip_rate_limit import enforce_ip_rate_limit

    await enforce_ip_rate_limit(
        request,
        bucket="signup_rl",
        limit=10,
        window_s=3600,
        redis=getattr(request.app.state, "_redis", None),
        detail="Too many signup attempts from this IP. Try again later.",
    )

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


@router.get("/stream-token")
async def get_stream_token(
    ctx: TenantContext = Depends(_require_tenant),
) -> JSONResponse:
    """Mint a short-lived, read-only token for SSE/EventSource connections.

    EventSource cannot send headers, so browsers pass this token as ``?token=`` on
    stream URLs instead of the permanent API key — keeping the key out of URLs,
    access logs, and proxy caches. The token is bound to this tenant, is read-only,
    and expires quickly.
    """
    from app.auth.stream_tokens import STREAM_TOKEN_TTL, mint_stream_token

    token = mint_stream_token(tenant_id=ctx.tenant_id, key_id=ctx.api_key_id or "")
    return JSONResponse({"token": token, "expires_in": STREAM_TOKEN_TTL})


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
    scopes = _scopes_for_new_key(ctx, body.scopes)
    limited = await _api_key_limit_denial(svc, ctx)
    if limited is not None:
        return limited
    try:
        result = await svc.create_api_key(
            tenant_id=ctx.tenant_id,
            name=body.name,
            scopes=scopes,
            expires_at=body.expires_at,
        )
    except NotFoundError as exc:
        return JSONResponse(exc.to_dict(), status_code=404)
    except PlatformError as exc:
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)
    return JSONResponse(result, status_code=201)


async def _api_key_limit_denial(
    svc: Any, ctx: TenantContext, *, replacing: str | None = None
) -> JSONResponse | None:
    """429 when the plan's ``max_api_keys`` active keys already exist.

    ``check_api_key_limit`` existed but nothing called it: any plan could mint
    unlimited keys. *replacing* is a key that the same request revokes (rotation),
    so it does not count.
    """
    from app.tenancy.limits import PlanLimitExceededError, check_api_key_limit

    try:
        keys = await svc.list_api_keys(ctx.tenant_id)
    except PlatformError as exc:
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)
    if isinstance(keys, dict):  # tolerate a {"keys": [...]} envelope
        keys = keys.get("keys", [])
    active = [k for k in keys if k.get("is_active", True) and k.get("key_id") != replacing]
    try:
        check_api_key_limit(ctx, len(active))
    except PlanLimitExceededError as exc:
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)
    return None


def _scopes_for_new_key(ctx: TenantContext, requested: list[str]) -> list[str]:
    """A scope-restricted key may only mint keys within its own scopes.

    Otherwise a key narrowed to e.g. ``tenancy:write`` could create an unscoped
    key and escape its restriction. An unrestricted caller keeps the old
    behaviour; a restricted caller that requests no scopes gets its own.
    """
    if not ctx.scopes:
        return requested
    if not requested:
        return list(ctx.scopes)
    extra = sorted(set(requested) - set(ctx.scopes))
    if extra:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot grant scopes this API key does not hold: {extra}",
        )
    return requested


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
    except PlatformError as exc:
        # Key store unavailable: the key may still be active — never report 204.
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)
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
    limited = await _api_key_limit_denial(svc, ctx, replacing=key_id if body.revoke_old else None)
    if limited is not None:
        return limited

    # Create the replacement key first so callers can take it before the old one
    # is revoked — minimising the window without a valid key.
    try:
        new_key = await svc.create_api_key(
            tenant_id=ctx.tenant_id,
            name=body.name,
            scopes=_scopes_for_new_key(ctx, body.scopes),
            expires_at=None,
        )
    except PlatformError as exc:
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)

    old_revoked = False
    revoke_error: str | None = None
    if body.revoke_old:
        # Don't fail the rotation (the new key's secret is only shown once), but
        # report honestly whether the old key was actually revoked — this used to
        # claim old_revoked=true even when the revoke failed.
        try:
            await svc.revoke_api_key(tenant_id=ctx.tenant_id, key_id=key_id)
            old_revoked = True
        except Exception as exc:
            revoke_error = str(exc) or type(exc).__name__

    payload: dict[str, Any] = {
        "new_key": new_key,
        "old_key_id": key_id,
        "old_revoked": old_revoked,
    }
    if revoke_error is not None:
        payload["revoke_error"] = revoke_error
    return JSONResponse(payload, status_code=201)


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


def _llm_store(request: Request) -> Any:
    from app.services.llm_config_store import get_llm_config_store

    return getattr(request.app.state, "llm_config_store", None) or get_llm_config_store()


async def _read_llm_config(request: Request, tenant_id: str) -> dict[str, Any] | None:
    store = _llm_store(request)
    if store is not None:
        cfg = await store.get_config(tenant_id)
        if cfg is not None:
            return dict(cfg)
    # No store wired (tests / no infrastructure): the process-local copy.
    local = getattr(request.app.state, "_llm_configs", {}).get(tenant_id)
    return dict(local) if local else None


def _safe_llm_view(tenant_id: str, cfg: dict[str, Any] | None) -> dict[str, Any]:
    if cfg is None:
        return {"tenant_id": tenant_id, "provider": None, "configured": False}
    # Never return the raw key or the vault-encrypted ciphertext.
    safe = {
        k: v
        for k, v in cfg.items()
        if k not in {"api_key", "encrypted_key", "vault_key_fingerprint"}
    }
    safe.setdefault("default_model", safe.get("model"))
    return {"tenant_id": tenant_id, **safe, "configured": True}


def _can_edit_llm(ctx: TenantContext) -> bool:
    from app.tenancy.rbac import has_role

    return has_role(ctx, "admin")


def _check_llm_base_url(base_url: str) -> None:
    from app.providers.tenant_provider import (
        TenantProviderError,
        _assert_tenant_base_url_allowed,
    )

    try:
        _assert_tenant_base_url_allowed(base_url)
    except TenantProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _audit_llm_change(
    request: Request,
    ctx: TenantContext,
    *,
    provider: str,
    base_url: str | None,
    key_changed: bool,
) -> None:
    """Record a tenant LLM provider change (who, provider, base_url — never the key)."""
    from app.governance.audit import AuditEvent
    from app.governance.permissions import ActionLevel

    audit_log = getattr(request.app.state, "audit_log", None)
    if audit_log is None:
        return
    audit_log.record(
        AuditEvent(
            goal_id="tenant_settings",
            tool_name="tenant.llm_config",
            action_level=ActionLevel.ALLOW_LOG,
            outcome="updated",
            api_key_id=ctx.api_key_id,
            note=f"provider={provider} base_url={base_url or ''} key_changed={key_changed}",
        ),
        tenant_ctx=ctx,
    )


async def _save_llm_config(
    request: Request,
    tenant_id: str,
    *,
    provider: str,
    encrypted_key: str,
    model: str,
    base_url: str | None,
    masked_key: str | None,
    vault_key_fingerprint: str | None = None,
) -> None:
    from app.services.llm_config_store import LLMConfigPersistError

    store = _llm_store(request)
    if store is not None:
        try:
            await store.set_config(
                tenant_id=tenant_id,
                provider=provider,
                encrypted_key=encrypted_key,
                model=model,
                base_url=base_url,
                masked_key=masked_key,
                vault_key_fingerprint=vault_key_fingerprint,
            )
        except LLMConfigPersistError as exc:
            raise HTTPException(
                status_code=503, detail="LLM configuration could not be saved"
            ) from exc
    if not hasattr(request.app.state, "_llm_configs"):
        request.app.state._llm_configs = {}
    # Process-local copy only for deployments without a store (tests, no infra);
    # the goal path reads the store first.
    request.app.state._llm_configs[tenant_id] = {
        "provider": provider,
        "base_url": base_url,
        "default_model": model,
        "masked_key": masked_key,
        "encrypted_key": encrypted_key,
        "vault_key_fingerprint": vault_key_fingerprint,
    }


@router.get("/me/llm")
async def get_llm_config(
    request: Request,
    ctx: TenantContext = Depends(_require_tenant),
) -> JSONResponse:
    """Return the current LLM provider config for this tenant (key never exposed)."""
    cfg = await _read_llm_config(request, ctx.tenant_id)
    return JSONResponse({**_safe_llm_view(ctx.tenant_id, cfg), "can_edit": _can_edit_llm(ctx)})


@router.put("/me/llm", status_code=200)
async def set_llm_config(
    body: LLMProviderConfig,
    request: Request,
    ctx: TenantContext = Depends(_require_tenant),
    _: None = Depends(require_role("admin")),
) -> JSONResponse:
    """Configure the LLM provider for this tenant. The API key is stored encrypted.

    Admin only: the provider key and base_url decide where every prompt (goal
    data, tool outputs) is sent. Each change is written to the audit trail.

    Durable in Postgres (tenant_llm_configs) with Redis as a cache. It used to
    be kept in this replica's memory (which the goal path read) plus Redis, so
    the provider applied only on the replica that handled this request.
    """
    if body.base_url:
        _check_llm_base_url(body.base_url)
    # PROV-15: the tenant's own vault key when it set one, else the platform vault.
    from app.providers.tenant_vault import TenantVaultError, encrypt_tenant_secret

    try:
        encrypted_key = await encrypt_tenant_secret(
            getattr(request.app.state, "db_session_factory", None), ctx.tenant_id, body.api_key
        )
    except TenantVaultError as exc:
        raise HTTPException(503, f"Tenant vault key could not be read: {exc}") from exc
    masked_key = body.api_key[:8] + "..." + body.api_key[-4:] if len(body.api_key) > 12 else "****"
    from app.providers.vault import get_vault

    await _save_llm_config(
        request,
        ctx.tenant_id,
        provider=body.provider,
        encrypted_key=encrypted_key,
        model=body.default_model or "",
        base_url=body.base_url,
        masked_key=masked_key,
        # BYOK-2: which platform key sealed it (or wraps the tenant key) — a
        # non-secret fingerprint, so a worker with another key can say so.
        vault_key_fingerprint=get_vault().fingerprint(),
    )
    _audit_llm_change(
        request, ctx, provider=body.provider, base_url=body.base_url, key_changed=True
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


# ── LLM config: non-secret fields ────────────────────────────────────────────


@router.get("/me/llm-config")
async def get_tenant_llm_config(request: Request) -> dict:
    """The tenant's LLM configuration without secrets (same record as /me/llm)."""
    tenant = _require_tenant(request)
    cfg = await _read_llm_config(request, tenant.tenant_id)
    return {**_safe_llm_view(tenant.tenant_id, cfg), "can_edit": _can_edit_llm(tenant)}


@router.put("/me/llm-config")
async def save_tenant_llm_config(
    request: Request, _: None = Depends(require_role("admin"))
) -> dict:
    """Update the non-secret fields (provider, default_model, base_url).

    This used to call TenantService methods that do not exist and answer
    ``{"status": "saved_in_memory"}`` without saving anything. The API key is
    set only through PUT /tenants/me/llm; with no key configured yet this is a
    409 rather than a config that can never authenticate.
    """
    tenant = _require_tenant(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="JSON body required") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="JSON object required")
    current = await _read_llm_config(request, tenant.tenant_id)
    if not current or not current.get("encrypted_key"):
        raise HTTPException(
            status_code=409,
            detail="No LLM API key configured; set it with PUT /tenants/me/llm first",
        )
    provider = str(body.get("provider") or current.get("provider") or "")
    model = str(
        body.get("default_model") or body.get("model")
        or current.get("model") or current.get("default_model") or ""
    )
    base_url = body.get("base_url", current.get("base_url"))
    if base_url is not None and not isinstance(base_url, str):
        raise HTTPException(status_code=422, detail="base_url must be a string")
    if base_url and base_url != current.get("base_url"):
        # This route skipped the allow-list PUT /me/llm applies.
        _check_llm_base_url(base_url)
    await _save_llm_config(
        request,
        tenant.tenant_id,
        provider=provider,
        encrypted_key=str(current["encrypted_key"]),
        model=model,
        base_url=base_url,
        masked_key=current.get("masked_key"),
        vault_key_fingerprint=current.get("vault_key_fingerprint"),
    )
    _audit_llm_change(request, tenant, provider=provider, base_url=base_url, key_changed=False)
    saved = await _read_llm_config(request, tenant.tenant_id)
    return {"status": "saved", **_safe_llm_view(tenant.tenant_id, saved)}


# ── Provider catalog (capabilities only — never returns secrets) ──────────────

_PROVIDER_CAPABILITIES: dict[str, dict[str, bool]] = {
    "anthropic": {"text": True, "tool_use": True, "vision": True, "streaming": True},
    "openai": {
        "text": True,
        "tool_use": True,
        "vision": True,
        "streaming": True,
        "embedding": True,
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


async def _tenant_byok_provider_name(request: Request, tenant_id: str) -> str | None:
    """The provider of the tenant's own LLM config, when it carries a key."""
    from app.providers.tenant_provider import _normalise
    from app.services.llm_config_store import get_llm_config_store

    store = getattr(request.app.state, "llm_config_store", None) or get_llm_config_store()
    if store is None:
        return None
    try:
        cfg = await store.get_config(tenant_id)
    except Exception:
        return None
    if not cfg or not str(cfg.get("encrypted_key") or ""):
        return None
    return _normalise(str(cfg.get("provider") or "")) or None


@router.get("/me/providers")
async def get_provider_catalog(request: Request) -> dict:
    """Return provider capabilities and config state — never returns secrets.

    ``configured_by`` says who configured a provider: ``"tenant"`` for the
    tenant's own key (BYOK, PUT /tenants/me/llm — it used to be reported as
    unconfigured), ``"platform"`` for a deployment env key, else ``None``.
    """
    import os

    ctx = _require_tenant(request)
    tenant_provider = await _tenant_byok_provider_name(request, ctx.tenant_id)

    providers = []
    for provider_name, caps in _PROVIDER_CAPABILITIES.items():
        env_key_name = _PROVIDER_ENV_KEYS.get(provider_name)
        platform_configured = (
            env_key_name is None  # No key needed (e.g. Ollama)
            or bool(os.getenv(env_key_name, ""))
        )
        configured_by = (
            "tenant"
            if provider_name == tenant_provider
            else ("platform" if platform_configured else None)
        )
        providers.append(
            {
                "name": provider_name,
                "display_name": provider_name.replace("_", " ").title(),
                "configured": configured_by is not None,
                "configured_by": configured_by,
                "capabilities": caps,
                "env_var": env_key_name,  # name only, never the value
            }
        )

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

        async with db() as session, sqlalchemy_rls_context(session, ctx.tenant_id):
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
        # Was a 201 with a fabricated id for an assignment stored nowhere.
        raise HTTPException(status_code=503, detail="Role store unavailable (no database)")
    try:
        from app.db.models.rbac import UserRole
        from app.db.rls import sqlalchemy_rls_context

        row = UserRole(
            id=role_id,
            tenant_id=ctx.tenant_id,
            user_id=body.user_id,
            role=body.role,
        )
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, ctx.tenant_id):
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
        raise HTTPException(status_code=503, detail="Role store unavailable (no database)")
    try:
        from sqlalchemy import select

        from app.db.models.rbac import UserRole
        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, session.begin(), sqlalchemy_rls_context(session, ctx.tenant_id):
            result = await session.execute(
                select(UserRole).where(
                    UserRole.id == role_id,
                    UserRole.tenant_id == ctx.tenant_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Role assignment not found")
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


async def _invalidate_ip_allowlist_cache(request: Request, tenant_id: str) -> None:
    """Drop the cached allowlist that ScopeEnforcementMiddleware enforces from.

    The Redis entry (``ip_wl:{tenant}``, 60 s TTL) is shared by every replica, so
    deleting it makes the change effective cluster-wide on the next request. The
    in-process fallback copy (used only while Redis is unreachable) is dropped on
    this replica; other replicas' copies expire within their TTL.
    """
    from app.auth import scope_enforcement
    from app.auth.ip_allowlist import IPAllowlistCache

    scope_enforcement._local_ip_allowlist_cache.pop(tenant_id, None)
    redis = getattr(request.app.state, "_rate_limiter_redis", None)
    if redis is None:
        return
    try:
        await IPAllowlistCache(redis).invalidate(tenant_id)
    except Exception as exc:
        # The DB change is committed; the stale entry expires within its 60 s TTL.
        import logging

        logging.getLogger(__name__).warning(
            "ip_allowlist_cache_invalidation_failed tenant=%s: %s", tenant_id, exc
        )


# The API used to read/write the legacy ``ip_allowlist`` table while enforcement
# (IPAllowlistCache) reads ``ip_allowlist_entries``: every CIDR a tenant added was
# listed back to it but never enforced. All three routes now use the enforced
# table (``app.db.models.auth.IPAllowlistEntry``) under the tenant's RLS context.


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

        from app.db.models.auth import IPAllowlistEntry
        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, session.begin(), sqlalchemy_rls_context(
            session, ctx.tenant_id
        ):
            result = await session.execute(
                select(IPAllowlistEntry).where(
                    IPAllowlistEntry.tenant_id == ctx.tenant_id,
                    IPAllowlistEntry.is_active.is_(True),
                )
            )
            rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "cidr": r.cidr,
                "description": r.label or "",
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
    """Add a CIDR range to this tenant's IP allowlist (enforced on every request)."""
    import uuid

    db = getattr(request.app.state, "db_session_factory", None)
    entry_id = uuid.uuid4().hex
    if db is None:
        # Was a 201 for a CIDR stored (and therefore enforced) nowhere.
        raise HTTPException(status_code=503, detail="IP allowlist store unavailable (no database)")
    try:
        from app.db.models.auth import IPAllowlistEntry
        from app.db.rls import sqlalchemy_rls_context

        row = IPAllowlistEntry(
            id=entry_id,
            tenant_id=ctx.tenant_id,
            cidr=body.cidr,
            label=body.description or None,
            is_active=True,
            created_by=ctx.api_key_id or None,
        )
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, ctx.tenant_id):
            session.add(row)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await _invalidate_ip_allowlist_cache(request, ctx.tenant_id)
    return {"id": entry_id, "cidr": body.cidr, "description": body.description}


# ── Tenant membership ─────────────────────────────────────────────────────────


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InviteMemberRequest(BaseModel):
    email: str
    role: str = "viewer"  # one of VALID_ROLES: admin | operator | approver | viewer

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip()
        if len(v) > 320 or not _EMAIL_RE.match(v):
            raise ValueError("invalid email address")
        return v

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        # Prevent role-injection: only known RBAC roles may be granted.
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(VALID_ROLES)}, got {v!r}")
        return v


@router.get("/me/members")
async def list_members(request: Request) -> dict:
    """List all members of the current tenant (from tenant_memberships)."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")

    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        # No database wired (in-memory app): honestly report no persisted members.
        return {"members": [], "tenant_id": tenant_ctx.tenant_id}

    from sqlalchemy import select

    from app.db.models.user import TenantMembership, User
    from app.db.rls import sqlalchemy_rls_context

    members: list[dict] = []
    async with db() as session, session.begin(), sqlalchemy_rls_context(
        session, tenant_ctx.tenant_id
    ):
        result = await session.execute(
            select(TenantMembership, User)
            .join(User, User.id == TenantMembership.user_id)
            .where(TenantMembership.tenant_id == tenant_ctx.tenant_id)
        )
        for m, u in result.all():
            members.append(
                {
                    "membership_id": m.id,
                    "user_id": u.id,
                    "email": u.email,
                    "name": u.name,
                    "role": m.role,
                    "status": m.status,
                    "invited_by": m.invited_by,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
            )
    return {"members": members, "tenant_id": tenant_ctx.tenant_id}


@router.post("/me/members/invite")
async def invite_member(
    body: InviteMemberRequest,
    request: Request,
    _: None = Depends(require_role("admin")),
) -> dict:
    """Invite a user to the tenant — persists a pending tenant_memberships row.

    Requires the admin role (membership management is privileged): this prevents
    privilege-escalation and cross-user role tampering by non-admin callers.
    """
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")

    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        # Be honest instead of returning a fabricated "invited": nothing was stored.
        raise HTTPException(
            status_code=503,
            detail="Membership store unavailable (no database configured); invite not created.",
        )

    from sqlalchemy import select

    from app.db.models.user import TenantMembership, User
    from app.db.rls import sqlalchemy_rls_context

    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant_ctx.tenant_id
        ):
            user = (
                await session.execute(select(User).where(User.email == body.email))
            ).scalar_one_or_none()
            if user is None:
                # Global identity keyed by email; membership below scopes it to this tenant.
                user = User(email=body.email)
                session.add(user)
                await session.flush()

            membership = (
                await session.execute(
                    select(TenantMembership).where(
                        TenantMembership.user_id == user.id,
                        TenantMembership.tenant_id == tenant_ctx.tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if membership is None:
                membership = TenantMembership(
                    user_id=user.id,
                    tenant_id=tenant_ctx.tenant_id,
                    role=body.role,
                    status="pending",
                )
                session.add(membership)
                await session.flush()
            else:
                # Re-inviting an existing member updates role and re-opens the invite.
                membership.role = body.role
                membership.status = "pending"
            membership_id = membership.id
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Best-effort email notification (depends on SMTP/notification service config).
    try:
        _notif_svc = getattr(request.app.state, "notification_service", None)
        if _notif_svc is not None and hasattr(_notif_svc, "send_invite"):
            await _notif_svc.send_invite(
                {"email": body.email, "role": body.role, "tenant_id": tenant_ctx.tenant_id}
            )
    except Exception:
        pass

    return {
        "status": "invited",
        "invitation_id": membership_id,
        "membership_id": membership_id,
        "email": body.email,
        "role": body.role,
        "tenant_id": tenant_ctx.tenant_id,
        "message": "Invitation persisted (pending). Email delivery depends on SMTP configuration.",
    }


# ── BYOK vault key management ─────────────────────────────────────────────────


class VaultKeyRequest(BaseModel):
    key_base64: str  # Customer-provided 32-byte key, base64-encoded


@router.post("/me/vault-key")
async def set_byok_vault_key(
    request: Request,
    body: VaultKeyRequest,
    ctx: TenantContext = Depends(_require_tenant),
) -> dict:
    """Set a Bring-Your-Own-Key (BYOK) key for this tenant's secret vault.

    Stored wrapped by the platform vault (envelope encryption); the tenant's
    secrets (LLM API key, connector secrets, OAuth tokens, source credentials,
    trigger secrets) are encrypted with it. Replacing the key keeps the previous
    keys for decryption only; older values are re-wrapped as they are read.
    """
    import base64 as _b64

    try:
        key_bytes = _b64.b64decode(body.key_base64, validate=True)
        if len(key_bytes) != 32:
            raise ValueError("Key must be 32 bytes when decoded")
    except Exception as exc:
        raise HTTPException(400, f"Invalid key: {exc}") from exc

    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(
            503, "Tenant vault keys need the database; none is configured on this deployment"
        )
    from app.providers.tenant_vault import TenantVaultError, store_tenant_vault_key

    try:
        fingerprint = await store_tenant_vault_key(db, ctx.tenant_id, key_bytes)
    except TenantVaultError as exc:
        raise HTTPException(503, f"Tenant vault key could not be stored: {exc}") from exc
    return {
        "status": "stored",
        "key_length": len(key_bytes),
        "persisted": True,
        "fingerprint": fingerprint,
        "message": "Stored (wrapped by the platform vault); used for this tenant's secrets.",
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
        raise HTTPException(status_code=503, detail="IP allowlist store unavailable (no database)")
    try:
        from sqlalchemy import select

        from app.db.models.auth import IPAllowlistEntry
        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, session.begin(), sqlalchemy_rls_context(session, ctx.tenant_id):
            result = await session.execute(
                select(IPAllowlistEntry).where(
                    IPAllowlistEntry.id == entry_id,
                    IPAllowlistEntry.tenant_id == ctx.tenant_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Allowlist entry not found")
            await session.delete(row)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await _invalidate_ip_allowlist_cache(request, ctx.tenant_id)


# ── Notification preferences ──────────────────────────────────────────────────


class A2ADirectorySetting(BaseModel):
    enabled: bool


@router.get("/me/a2a-directory")
async def get_a2a_directory(
    request: Request, ctx: TenantContext = Depends(_require_tenant)
) -> dict[str, bool]:
    """Whether this tenant's opted-in agents are listed in the public A2A
    directory (``/.well-known/agents``). Off by default (D3)."""
    from app.services.a2a_directory import DirectoryUnavailableError, directory_for

    try:
        enabled = await directory_for(request.app.state).tenant_enabled(ctx.tenant_id)
    except DirectoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"enabled": enabled}


@router.put("/me/a2a-directory")
async def set_a2a_directory(
    body: A2ADirectorySetting,
    request: Request,
    ctx: TenantContext = Depends(_require_tenant),
    _: None = Depends(require_role("admin")),
) -> dict[str, bool]:
    """Turn the tenant's public A2A directory listing on or off (admin only).

    Only agents that also opted in (``a2a_public``) and are active are listed;
    turning it off hides every card at once.
    """
    from app.services.a2a_directory import DirectoryUnavailableError, directory_for

    try:
        await directory_for(request.app.state).set_tenant_enabled(ctx.tenant_id, body.enabled)
    except DirectoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"enabled": body.enabled}


_NOTIFICATION_KEYS = frozenset(
    {"goalComplete", "goalFailed", "budgetAlert", "hitlPending", "weeklyReport"}
)


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
        import json

        try:
            stored = await redis.get(f"notif_prefs:{tenant.tenant_id}")
        except Exception as exc:
            # Not the defaults: that would show "saved" preferences as reset.
            raise HTTPException(
                status_code=503, detail="Notification preferences unavailable"
            ) from exc
        if stored:
            prefs.update(json.loads(stored))
    return prefs


@router.put("/me/notifications")
async def update_notifications(request: Request) -> dict:
    """Update tenant notification preferences."""
    tenant = _require_tenant(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Body must be a JSON object") from exc
    if not isinstance(body, dict) or not all(
        k in _NOTIFICATION_KEYS and isinstance(v, bool) for k, v in body.items()
    ):
        raise HTTPException(
            status_code=422,
            detail=f"Body must map {sorted(_NOTIFICATION_KEYS)} to booleans",
        )

    # "updated" used to be returned with no Redis wired and on a Redis error,
    # and saved preferences silently expired after 30 days.
    redis = getattr(request.app.state, "_redis", None)
    if redis is None:
        raise HTTPException(status_code=503, detail="Notification preferences store unavailable")
    import json

    try:
        await redis.set(f"notif_prefs:{tenant.tenant_id}", json.dumps(body))
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Notification preferences could not be saved"
        ) from exc
    return {"status": "updated", "preferences": body}


# ── Sessions ──────────────────────────────────────────────────────────────────


@router.get("/me/sessions")
async def list_sessions(request: Request) -> None:
    """List login sessions — not implemented (501).

    This always returned ``[]`` although nothing records sessions, so the UI
    showed "no other sessions" as if that were verified. See app/api/sessions.py.
    """
    from app.api.sessions import raise_sessions_not_implemented

    _require_tenant(request)
    raise_sessions_not_implemented(request)


@router.delete("/me/sessions/{session_id}")
async def revoke_tenant_session(session_id: str, request: Request) -> None:
    """Revoke a login session — not implemented (501); the UI calls this path."""
    from app.api.sessions import raise_sessions_not_implemented

    _require_tenant(request)
    raise_sessions_not_implemented(request)


# ── Data export ───────────────────────────────────────────────────────────────


_EXPORT_PAGE_SIZE = 200


async def _collect_all_pages(
    fetch_page: Any, *, id_keys: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Page through ``fetch_page(limit, offset)`` until it returns an empty page.

    Stops on an empty page rather than a short one, so a backend that clamps
    ``limit`` below the requested size is still read to the end. Rows are
    de-duplicated by the first present key in ``id_keys`` (offset paging over a
    live table can repeat a row when new rows are inserted mid-export), and a
    page that adds nothing new ends the loop so a backend that ignores
    ``offset`` cannot spin forever.
    """
    rows: list[dict[str, Any]] = []
    seen: set[Any] = set()
    offset = 0
    while True:
        page = await fetch_page(_EXPORT_PAGE_SIZE, offset)
        if not page:
            return rows
        added = 0
        for row in page:
            key = next((row[k] for k in id_keys if isinstance(row, dict) and k in row), None)
            if key is not None and key in seen:
                continue
            if key is not None:
                seen.add(key)
            rows.append(row)
            added += 1
        if added == 0:
            return rows
        offset += len(page)


@router.post("/me/export")
async def export_tenant_data(request: Request) -> dict:
    """Export all of the tenant's goals and agents as JSON (GDPR data export).

    The export is complete or it fails: every goal and agent is read by paging
    through the tenant-scoped (RLS) service reads -- not just their first page
    -- and a missing service or read failure answers **503** instead of an
    export that silently omits data.
    """
    tenant = _require_tenant(request)
    goal_svc = getattr(request.app.state, "goal_service", None)
    agent_store = getattr(request.app.state, "agent_store", None)
    if goal_svc is None or agent_store is None:
        raise HTTPException(
            status_code=503,
            detail="Data export unavailable: goal service or agent store is not configured",
        )

    async def _goal_page(limit: int, offset: int) -> list[dict[str, Any]]:
        resp = await goal_svc.list_goals(tenant_ctx=tenant, limit=limit, offset=offset)
        return list(resp.get("goals", [])) if isinstance(resp, dict) else []

    async def _agent_page(limit: int, offset: int) -> list[dict[str, Any]]:
        return list(await agent_store.list_async(tenant_ctx=tenant, limit=limit, offset=offset))

    try:
        goals = await _collect_all_pages(_goal_page, id_keys=("id", "goal_id"))
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Data export failed: goals could not be read"
        ) from exc
    try:
        agents = await _collect_all_pages(_agent_page, id_keys=("agent_id", "id"))
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Data export failed: agents could not be read"
        ) from exc

    return {
        "tenant_id": tenant.tenant_id,
        "exported_at": datetime.now(UTC).isoformat(),
        "goals": goals,
        "agents": agents,
        "counts": {"goals": len(goals), "agents": len(agents)},
    }


# ── Account deletion ──────────────────────────────────────────────────────────


@router.delete("/me")
async def delete_tenant(
    request: Request,
    # Erasing the whole tenant is admin-only (any key could schedule it).
    _: None = Depends(require_role("admin")),
) -> dict:
    """Schedule the current tenant's deletion (durable GDPR erasure job).

    This returned ``scheduled_for_deletion`` and did nothing. It now records the
    same erasure job as ``POST /enterprise/compliance/delete`` (executed by the
    ``process_tenant_erasures`` beat task after the grace period) and answers
    503 when the job could not be recorded.
    """
    tenant = _require_tenant(request)
    from app.api._deps import get_compliance_controller

    try:
        job: dict = await get_compliance_controller(request).request_data_deletion(
            tenant_ctx=tenant
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Deletion request could not be recorded; retry."
        ) from exc
    return {**job, "status": "scheduled_for_deletion", "tenant_id": tenant.tenant_id}
