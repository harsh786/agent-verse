"""Tenant email settings (owner decision a02-F036-02), admin only.

* ``GET    /tenants/me/email`` — allowlist + SMTP sender (secret masked) + relay in use.
* ``PUT    /tenants/me/email/allowlist`` — replace the recipient allowlist (empty = off).
* ``PUT    /tenants/me/email/smtp`` — configure the tenant-owned SMTP sender.
* ``DELETE /tenants/me/email/smtp`` — remove it (back to the platform relay).
* ``POST   /tenants/me/email/smtp/test`` — connect / authenticate (and optionally
  send a test message) with the saved or a candidate configuration.

Every change is audited (before it is made; never with a secret). The SMTP
password / API key is write-only: no response carries it or its ciphertext.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from app.tenancy.context import TenantContext
from app.tenancy.rbac import require_role

router = APIRouter(prefix="/tenants/me/email", tags=["tenants"])

_AUDIT_GOAL = "tenant_settings"


def _ctx(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return ctx


class AllowlistRequest(BaseModel):
    entries: list[str] = Field(default_factory=list, max_length=1000)


class SMTPConfigRequest(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(587, ge=1, le=65535)
    tls_mode: Literal["starttls", "tls", "none"] = "starttls"
    username: str = Field("", max_length=320)
    # Password or API key. Write-only; omit it to keep the stored one (only for
    # the same host, port and username).
    secret: str | None = Field(None, max_length=4096)
    from_address: str = Field(min_length=3, max_length=320)

    @field_validator("host")
    @classmethod
    def _norm_host(cls, v: str) -> str:
        return v.strip().lower().rstrip(".")

    @field_validator("username", "from_address")
    @classmethod
    def _strip(cls, v: str) -> str:
        if "\r" in v or "\n" in v:
            raise ValueError("must not contain line breaks")
        return v.strip()


class SMTPTestRequest(BaseModel):
    # Test unsaved settings; omitted → the saved configuration.
    config: SMTPConfigRequest | None = None
    # Also send a short test message to this address (allowlist + quota apply).
    send_to: str | None = Field(None, max_length=320)


async def _audit(
    request: Request, ctx: TenantContext, *, tool: str, outcome: str, note: str, durable: bool
) -> None:
    from app.api.tools import _audit_native

    await _audit_native(
        request, ctx, tool=tool, outcome=outcome, note=note, durable=durable, goal_id=_AUDIT_GOAL
    )


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


@router.get("")
async def get_email_settings(
    request: Request, _: None = Depends(require_role("admin"))
) -> dict[str, Any]:
    """The tenant's email settings. The SMTP secret is never returned (masked)."""
    from app.services.tenant_email_settings import (
        EmailSettingsUnavailableError,
        email_settings_for,
    )

    ctx = _ctx(request)
    try:
        settings = await email_settings_for(request.app.state).get(ctx.tenant_id)
    except EmailSettingsUnavailableError as exc:
        raise _unavailable(exc) from exc
    return settings.public_view()


@router.put("/allowlist")
async def set_email_allowlist(
    body: AllowlistRequest, request: Request, _: None = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Replace the recipient allowlist. Entries: ``alice@example.com``,
    ``example.com`` (that domain only) or ``*.example.com`` (its subdomains).
    Empty = no restriction (today's behaviour)."""
    from app.services.tenant_email_settings import (
        EmailSettingsUnavailableError,
        email_settings_for,
    )
    from app.tools.email_policy import AllowlistEntryError, normalize_allowlist

    ctx = _ctx(request)
    try:
        entries = normalize_allowlist(body.entries)
    except AllowlistEntryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _audit(
        request,
        ctx,
        tool="tenant.email_allowlist",
        outcome="updated",
        note=f"entries={len(entries)} enabled={bool(entries)}",
        durable=True,
    )
    try:
        saved = await email_settings_for(request.app.state).set_allowlist(
            ctx.tenant_id, entries, updated_by=ctx.api_key_id or None
        )
    except EmailSettingsUnavailableError as exc:
        await _audit(
            request, ctx, tool="tenant.email_allowlist", outcome="failed", note="", durable=False
        )
        raise _unavailable(exc) from exc
    return saved.public_view()


def _target(cfg: SMTPConfigRequest) -> Any:
    from app.tools.tenant_smtp import SMTPTarget

    return SMTPTarget(
        host=cfg.host,
        port=cfg.port,
        tls_mode=cfg.tls_mode,
        username=cfg.username,
        from_address=cfg.from_address,
    )


async def _validate_config(cfg: SMTPConfigRequest) -> None:
    """422 for a bad from-address, port, TLS mode, or a host the egress policy refuses."""
    from app.tools import tenant_smtp
    from app.tools.email_tool import _validate_email

    try:
        _validate_email(cfg.from_address)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"from_address: {exc}") from exc
    target = _target(cfg)
    try:
        tenant_smtp.validate_target(target)
        await asyncio.to_thread(tenant_smtp.check_host, target.host)
    except tenant_smtp.SMTPConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _same_endpoint(cfg: SMTPConfigRequest, current: Any) -> bool:
    return bool(
        current is not None
        and current.host == cfg.host
        and current.port == cfg.port
        and current.username == cfg.username
    )


@router.put("/smtp")
async def set_email_smtp(
    body: SMTPConfigRequest, request: Request, _: None = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Configure the tenant-owned SMTP sender used by the agent email tool.

    The secret is encrypted in the vault and never returned. Omitting it keeps
    the stored one only for the same host, port and username — a stored secret
    is never sent to another server than the one it was entered for. System
    mail (invites, password resets, alerts) keeps using the platform relay.
    """
    from app.services.tenant_email_settings import (
        EmailSettingsUnavailableError,
        TenantSMTPSettings,
        email_settings_for,
        seal_smtp_secret,
    )

    ctx = _ctx(request)
    await _validate_config(body)
    store = email_settings_for(request.app.state)
    try:
        current = (await store.get(ctx.tenant_id)).smtp
    except EmailSettingsUnavailableError as exc:
        raise _unavailable(exc) from exc
    new_secret = (body.secret or "").strip()
    secret_enc: str | None
    fingerprint: str | None
    if not body.username:
        if new_secret:
            raise HTTPException(422, "a secret needs a username (SMTP AUTH)")
        secret_enc, fingerprint = None, None
    elif new_secret:
        from app.providers.tenant_vault import TenantVaultError

        try:
            secret_enc, fingerprint = await seal_smtp_secret(
                store.db_factory, ctx.tenant_id, new_secret
            )
        except TenantVaultError as exc:
            raise HTTPException(503, "Tenant vault key could not be read") from exc
    elif _same_endpoint(body, current) and current is not None and current.secret_enc:
        secret_enc, fingerprint = current.secret_enc, current.vault_key_fingerprint
    else:
        raise HTTPException(
            status_code=422,
            detail=(
                "secret is required: none is stored for this host, port and username "
                "(a stored secret is only reused for the same server and username)"
            ),
        )
    await _audit(
        request,
        ctx,
        tool="tenant.email_smtp",
        outcome="updated",
        note=(
            f"host={body.host} port={body.port} tls_mode={body.tls_mode} "
            f"auth={bool(body.username)} secret_changed={bool(new_secret)}"
        ),
        durable=True,
    )
    try:
        saved = await store.set_smtp(
            ctx.tenant_id,
            TenantSMTPSettings(
                host=body.host,
                port=body.port,
                tls_mode=body.tls_mode,
                username=body.username,
                from_address=body.from_address,
                secret_enc=secret_enc,
                vault_key_fingerprint=fingerprint,
            ),
            updated_by=ctx.api_key_id or None,
        )
    except EmailSettingsUnavailableError as exc:
        await _audit(
            request, ctx, tool="tenant.email_smtp", outcome="failed", note="", durable=False
        )
        raise _unavailable(exc) from exc
    return saved.public_view()


@router.delete("/smtp")
async def delete_email_smtp(
    request: Request, _: None = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Remove the tenant SMTP sender: the agent email tool falls back to the
    platform relay (with its restrictions). The stored secret is deleted."""
    from app.services.tenant_email_settings import (
        EmailSettingsUnavailableError,
        email_settings_for,
    )

    ctx = _ctx(request)
    await _audit(request, ctx, tool="tenant.email_smtp", outcome="deleted", note="", durable=True)
    try:
        saved = await email_settings_for(request.app.state).clear_smtp(
            ctx.tenant_id, updated_by=ctx.api_key_id or None
        )
    except EmailSettingsUnavailableError as exc:
        raise _unavailable(exc) from exc
    return saved.public_view()


@router.post("/smtp/test")
async def probe_email_smtp(
    request: Request,
    body: SMTPTestRequest | None = None,
    _: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Connect to the SMTP server (TLS / STARTTLS, AUTH) with the saved or a
    candidate configuration; with ``send_to``, also send a short test message.

    The host passes the SSRF egress policy first. The answer names the stage
    that failed (policy / connect / tls / auth / send), never the credentials.
    A candidate without a secret reuses the stored one only for the same host,
    port and username.
    """
    from app.services.tenant_email_settings import (
        EmailSettingsUnavailableError,
        email_settings_for,
        open_smtp_secret,
    )
    from app.tools import email_policy, email_quota, email_tool, tenant_smtp

    ctx = _ctx(request)
    body = body or SMTPTestRequest()
    store = email_settings_for(request.app.state)
    try:
        settings = await store.get(ctx.tenant_id)
    except EmailSettingsUnavailableError as exc:
        raise _unavailable(exc) from exc
    stored = settings.smtp
    secret: str | None
    if body.config is not None:
        cfg = body.config
        await _validate_config(cfg)
        target = _target(cfg)
        secret = (cfg.secret or "").strip() or None
        if secret is None and cfg.username and _same_endpoint(cfg, stored) and stored:
            try:
                secret = await open_smtp_secret(store.db_factory, ctx.tenant_id, stored)
            except Exception as exc:
                raise HTTPException(503, "Stored SMTP secret could not be read") from exc
        tested = "candidate"
    elif stored is not None:
        target = tenant_smtp.SMTPTarget(
            host=stored.host,
            port=stored.port,
            tls_mode=stored.tls_mode,
            username=stored.username,
            from_address=stored.from_address,
        )
        try:
            secret = await open_smtp_secret(store.db_factory, ctx.tenant_id, stored)
        except Exception as exc:
            raise HTTPException(503, "Stored SMTP secret could not be read") from exc
        tested = "saved"
    else:
        raise HTTPException(status_code=404, detail="No SMTP sender is configured")

    message = None
    recipients: list[str] = []
    if body.send_to:
        send_to = body.send_to.strip()
        try:
            email_tool._validate_outgoing(
                target.from_address,
                [send_to],
                "AgentVerse SMTP test",
                from_addr=None,
                reply_to=None,
                sender_label="tenant SMTP sender",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if email_policy.disallowed_recipients([send_to], settings.recipient_allowlist):
            raise HTTPException(
                status_code=403,
                detail=f"{send_to} is not on this workspace's email allowlist; nothing was sent.",
            )
        try:
            await email_quota.consume(
                getattr(request.app.state, "_redis", None), ctx.tenant_id, ctx.plan, 1
            )
        except email_quota.EmailQuotaExceededError as exc:
            raise HTTPException(429, "Daily email recipient quota exhausted.") from exc
        except email_quota.EmailQuotaUnavailableError as exc:
            raise HTTPException(503, "Email quota store unavailable; nothing was sent.") from exc
        recipients = [send_to]
        message = email_tool._build_message(
            target.from_address,
            recipients,
            "AgentVerse SMTP test",
            "This is a test message from your AgentVerse workspace's SMTP settings.",
            reply_to=None,
            tenant_id=ctx.tenant_id,
        )
    await _audit(
        request,
        ctx,
        tool="tenant.email_smtp_test",
        outcome="requested",
        note=f"host={target.host} port={target.port} tested={tested} send={bool(message)}",
        durable=True,
    )
    outcome = await tenant_smtp.probe(target, secret, message=message, recipients=recipients)
    if not outcome.ok:
        await _audit(
            request,
            ctx,
            tool="tenant.email_smtp_test",
            outcome="failed",
            note=f"stage={outcome.stage} code={outcome.code}",
            durable=False,
        )
    return {**outcome.as_dict(), "tested": tested}
