"""Tenant self-service for messaging-gateway bindings (TRG-42).

Replaces the operator-only ``CHANNEL_TENANT_MAP`` env var (and the 501
``/v1/gateway/{org}/config`` stubs) with durable, tenant-scoped bindings stored
in ``channel_tenant_mappings`` (see :mod:`app.gateway.binding_store`):

* ``POST   /channels/bindings`` — bind a Telegram bot, WhatsApp number, Slack
  workspace, Teams organisation or a generic webhook. Ownership is proven
  first (:mod:`app.gateway.binding_verification`); a channel another tenant
  has verified is refused (409). Secrets are vault-encrypted at rest; a
  server-generated secret is returned ONCE.
* ``GET    /channels/bindings`` — the tenant's bindings (no secrets).
* ``DELETE /channels/bindings/{id}`` — unbind.

Every write bumps the Redis binding version, so every replica routes the change
on its next message.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.tenancy.rbac import require_role

router = APIRouter(prefix="/channels/bindings", tags=["channels"])

_GATEWAY_ONLY = ("telegram", "whatsapp", "webhook")


class BindingCreate(BaseModel):
    channel: str = Field(description="telegram | whatsapp | slack | teams | webhook")
    addressee: str = Field(
        default="",
        description=(
            "Telegram bot id, WhatsApp phone_number_id, Slack team id or Microsoft 365 "
            "tenant id; generated for a generic webhook"
        ),
    )
    secret: str = Field(
        default="",
        description=(
            "Inbound credential: Telegram secret_token (generated if empty), WhatsApp app "
            "secret, Slack signing secret, webhook HMAC key (generated if empty)"
        ),
    )
    outbound_token: str = Field(
        default="", description="Bot token used to prove ownership and send replies"
    )
    app_id: str = Field(default="", description="Teams: the tenant's Bot Framework app id")
    org_id: str = ""


def _tenant_id(request: Request) -> str:
    tenant_id = str(getattr(getattr(request.state, "tenant", None), "tenant_id", "") or "")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return tenant_id


def _dbs(request: Request) -> tuple[Any, Any]:
    from app.api.channels.ingestion import _lookup_db, _tenant_db

    tenant_db, system_db = _tenant_db(request), _lookup_db(request)
    if tenant_db is None or system_db is None:
        raise HTTPException(status_code=503, detail="Channel bindings need the database")
    return tenant_db, system_db


def _webhook_url(channel: str, addressee: str) -> str:
    return f"/v1/gateway/{channel}/chat/{addressee}"


async def _invalidate(request: Request) -> None:
    from app.gateway.binding_store import get_binding_store

    store = get_binding_store(request.app.state)
    if store is not None:
        await store.invalidate()


@router.post("", dependencies=[Depends(require_role("admin", "operator"))])
async def create_binding(body: BindingCreate, request: Request) -> dict[str, Any]:
    from app.api.channels.ingestion import _normalize_m365_tenant_id
    from app.api.channels.verification import STATUS_VERIFIED, _verified_by_other
    from app.gateway.binding_store import GATEWAY_CHANNELS, encrypt_secret
    from app.gateway.binding_verification import BindingOwnershipError, verify_ownership

    tenant_id = _tenant_id(request)
    tenant_db, system_db = _dbs(request)
    channel = body.channel.strip().lower()
    if channel not in GATEWAY_CHANNELS:
        raise HTTPException(422, f"channel must be one of {', '.join(GATEWAY_CHANNELS)}")
    addressee = body.addressee.strip()
    secret = body.secret.strip()
    generated_secret = False

    if channel == "webhook":
        addressee = f"wh-{secrets.token_hex(8)}"  # server-generated: cannot be squatted
    elif not addressee:
        raise HTTPException(422, "addressee is required")
    if channel == "teams":
        addressee = _normalize_m365_tenant_id(addressee)
        if not addressee:
            raise HTTPException(422, "Teams addressee must be your Microsoft 365 tenant id")
        if not body.app_id.strip():
            raise HTTPException(422, "app_id (your Bot Framework app id) is required for Teams")
    if channel in ("telegram", "webhook") and not secret:
        secret, generated_secret = secrets.token_urlsafe(32), True
    if channel in ("whatsapp", "slack") and not secret:
        raise HTTPException(
            422,
            "secret is required ("
            + ("the WhatsApp app secret" if channel == "whatsapp" else "the Slack signing secret")
            + ")",
        )

    if await _verified_by_other(system_db, tenant_id, channel, addressee):
        raise HTTPException(409, "This channel is already bound to another tenant")

    existing = await _own_mapping(tenant_db, tenant_id, channel, addressee)
    if channel == "teams":
        if existing is None or existing["status"] not in ("verified", "legacy_unverified"):
            raise HTTPException(
                409,
                "Verify your Microsoft 365 tenant under Channels first "
                "(POST /channels/mappings), then add the Teams binding",
            )
    elif channel in ("telegram", "whatsapp", "slack"):
        try:
            await verify_ownership(channel, addressee, outbound_token=body.outbound_token.strip())
        except BindingOwnershipError as exc:
            raise HTTPException(422, f"Ownership not proven: {exc}") from None

    config: dict[str, Any] = dict((existing or {}).get("channel_config") or {})
    config.update(
        {
            "gateway": True,
            "org_id": body.org_id.strip(),
            "secret_enc": encrypt_secret(secret),
            "outbound_token_enc": encrypt_secret(body.outbound_token.strip()),
            "app_id": body.app_id.strip(),
        }
    )
    mapping_id = await _upsert(tenant_db, tenant_id, channel, addressee, config, existing)
    if mapping_id is None:
        raise HTTPException(409, "This channel is already bound to another tenant")
    await _invalidate(request)
    response: dict[str, Any] = {
        "id": mapping_id,
        "channel": channel,
        "addressee": addressee,
        "status": STATUS_VERIFIED if channel != "teams" else existing["status"],  # type: ignore[index]
        "org_id": config["org_id"],
        "has_secret": bool(secret),
        "has_outbound_token": bool(body.outbound_token.strip()),
        "app_id": config["app_id"],
        "webhook_url": _webhook_url(channel, addressee),
    }
    if generated_secret:
        response["secret"] = secret  # shown once
    return response


async def _own_mapping(
    tenant_db: Any, tenant_id: str, channel: str, addressee: str
) -> dict[str, Any] | None:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        tenant_db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        row = (
            await session.execute(
                text(
                    "SELECT id, status, channel_config FROM channel_tenant_mappings "
                    "WHERE tenant_id = :tid AND channel_type = :ct AND channel_id = :ci"
                ),
                {"tid": tenant_id, "ct": channel, "ci": addressee},
            )
        ).first()
    if row is None:
        return None
    return {"id": str(row[0]), "status": str(row[1]), "channel_config": dict(row[2] or {})}


async def _upsert(
    tenant_db: Any,
    tenant_id: str,
    channel: str,
    addressee: str,
    config: dict[str, Any],
    existing: dict[str, Any] | None,
) -> str | None:
    """Write the binding; None when a concurrent rival verified the channel first
    (the partial unique index on routable mappings refuses the second)."""
    import json

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with (
            tenant_db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            if existing is not None:
                # Ownership was just proven (or, for Teams, the row is already
                # routable): the row becomes / stays verified.
                await session.execute(
                    text(
                        "UPDATE channel_tenant_mappings SET channel_config = CAST(:cfg AS jsonb), "
                        "status = CASE WHEN status IN ('verified', 'legacy_unverified') "
                        "THEN status ELSE 'verified' END, "
                        "verified_at = COALESCE(verified_at, now()), "
                        "verification_code_hash = NULL, verification_expires_at = NULL, "
                        "enabled = true, updated_at = now() "
                        "WHERE id = :id AND tenant_id = :tid"
                    ),
                    {"cfg": json.dumps(config), "id": existing["id"], "tid": tenant_id},
                )
                return str(existing["id"])
            mapping_id = uuid.uuid4().hex
            await session.execute(
                text(
                    "INSERT INTO channel_tenant_mappings "
                    "(id, tenant_id, channel_type, channel_id, status, verified_at, "
                    "channel_config) VALUES (:id, :tid, :ct, :ci, 'verified', now(), "
                    "CAST(:cfg AS jsonb))"
                ),
                {
                    "id": mapping_id,
                    "tid": tenant_id,
                    "ct": channel,
                    "ci": addressee,
                    "cfg": json.dumps(config),
                },
            )
            return mapping_id
    except IntegrityError:
        return None


@router.get("")
async def list_bindings(request: Request) -> list[dict[str, Any]]:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    tenant_id = _tenant_id(request)
    tenant_db, _ = _dbs(request)
    async with (
        tenant_db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        rows = await session.execute(
            text(
                "SELECT id, channel_type, channel_id, status, channel_config, created_at "
                "FROM channel_tenant_mappings WHERE tenant_id = :tid "
                "AND channel_config ->> 'gateway' = 'true' ORDER BY created_at"
            ),
            {"tid": tenant_id},
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            cfg = dict(r[4] or {})
            out.append(
                {
                    "id": str(r[0]),
                    "channel": str(r[1]),
                    "addressee": str(r[2]),
                    "status": str(r[3]),
                    "routable": str(r[3]) in ("verified", "legacy_unverified"),
                    "org_id": str(cfg.get("org_id") or ""),
                    "has_secret": bool(cfg.get("secret_enc")),
                    "has_outbound_token": bool(cfg.get("outbound_token_enc")),
                    "app_id": str(cfg.get("app_id") or ""),
                    "webhook_url": _webhook_url(str(r[1]), str(r[2])),
                    "created_at": r[5].isoformat() if r[5] is not None else None,
                }
            )
        return out


@router.delete("/{binding_id}", dependencies=[Depends(require_role("admin", "operator"))])
async def delete_binding(binding_id: str, request: Request) -> dict[str, Any]:
    """Telegram/WhatsApp/webhook rows exist only as bindings and are deleted;
    a Slack/Teams mapping keeps routing inbound channel events, so only its
    gateway settings (secrets included) are removed."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    tenant_id = _tenant_id(request)
    tenant_db, _ = _dbs(request)
    async with (
        tenant_db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        row = (
            await session.execute(
                text(
                    "SELECT channel_type FROM channel_tenant_mappings WHERE id = :id "
                    "AND tenant_id = :tid AND channel_config ->> 'gateway' = 'true' FOR UPDATE"
                ),
                {"id": binding_id, "tid": tenant_id},
            )
        ).first()
        if row is None:
            raise HTTPException(404, "Binding not found")
        if str(row[0]) in _GATEWAY_ONLY:
            await session.execute(
                text("DELETE FROM channel_tenant_mappings WHERE id = :id AND tenant_id = :tid"),
                {"id": binding_id, "tid": tenant_id},
            )
        else:
            await session.execute(
                text(
                    "UPDATE channel_tenant_mappings SET channel_config = channel_config "
                    "- 'gateway' - 'secret_enc' - 'outbound_token_enc' - 'app_id' - 'org_id', "
                    "updated_at = now() WHERE id = :id AND tenant_id = :tid"
                ),
                {"id": binding_id, "tid": tenant_id},
            )
    await _invalidate(request)
    return {"id": binding_id, "deleted": True}
