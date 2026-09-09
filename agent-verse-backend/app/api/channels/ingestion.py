"""Channel Ingestion API — routes for Slack, Teams, Discord, email, SMS, voice, forms."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["channels"])


# ── Dependency helpers ────────────────────────────────────────────────────────


def _get_gateway(request: Request) -> Any:
    return getattr(request.app.state, "channel_gateway", None)


def _get_store(request: Request) -> Any:
    return getattr(request.app.state, "schedule_store", None)


def _get_dispatcher(request: Request) -> Any:
    return getattr(request.app.state, "trigger_dispatcher", None)


def _channel_verified(request: Request, channel_type: str, *, slack_ok: bool = False) -> bool:
    """Whether an inbound channel request is authenticated well enough to fire
    triggers. Fail-closed: conversational triggers publish ONLY for a verified
    request, so a spoofed webhook (e.g. a forged X-Tenant-ID) cannot fire a
    victim tenant's triggers.

    * slack — HMAC signature verified (``slack_ok``, computed by the endpoint).
    * others — a per-channel shared secret in ``app.state.channel_webhook_secrets``
      must match the ``X-Webhook-Secret`` header. No secret configured → not
      verified (the channel's triggers stay dormant until an operator sets one).
    """
    if channel_type == "slack":
        return slack_ok
    secrets = getattr(request.app.state, "channel_webhook_secrets", None) or {}
    expected = str(secrets.get(channel_type, "") or "")
    provided = request.headers.get("X-Webhook-Secret", "")
    return bool(expected) and bool(provided) and hmac.compare_digest(provided, expected)


async def _emit_chat_event(
    request: Request, channel_type: str, body: dict, tenant_id: str, *, verified: bool
) -> None:
    """Publish a normalized conversational event onto the EVENT bus so Family C
    (chat/email/sms/voice/form) triggers can fire — only for a verified request."""
    redis = getattr(request.app.state, "trigger_event_redis", None)
    if redis is None or not tenant_id or not verified:
        return
    try:
        from app.triggers.consumers.conversational import (
            normalize_conversational_event,
            publish_conversational_event,
        )

        event = normalize_conversational_event(channel_type, body)
        await publish_conversational_event(redis, tenant_id=tenant_id, event=event)
    except Exception as exc:  # never break ingestion on a publish failure
        _log.warning("chat_event_publish_failed channel=%s: %s", channel_type, exc)


# ── Tenant resolution via channel mapping ────────────────────────────────────


async def _resolve_tenant_from_channel(
    channel_type: str,
    channel_id: str,
    db: Any,
) -> str | None:
    """Look up tenant_id from channel_tenant_mappings table."""
    if db is None:
        return None
    try:
        from sqlalchemy import text

        async with db() as session:
            row = await session.execute(
                text(
                    "SELECT tenant_id FROM channel_tenant_mappings "
                    "WHERE channel_type = :ct AND channel_id = :ci LIMIT 1"
                ),
                {"ct": channel_type, "ci": channel_id},
            )
            result = row.fetchone()
            return result[0] if result else None
    except Exception:
        return None


# ── Slack Events API ──────────────────────────────────────────────────────────


@router.post("/slack/events")
async def slack_events(
    request: Request,
    x_slack_signature: str = Header(default=""),
    x_slack_request_timestamp: str = Header(default=""),
) -> dict:
    """Handle Slack Events API webhook."""
    body_bytes = await request.body()
    body = json.loads(body_bytes)

    # Slack URL verification challenge
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    # Signature verification
    signing_secret = getattr(request.app.state, "slack_signing_secret", "")
    slack_verified = False
    if signing_secret and x_slack_signature:
        sig_basestring = f"v0:{x_slack_request_timestamp}:{body_bytes.decode()}"
        computed = (
            "v0="
            + hmac.new(signing_secret.encode(), sig_basestring.encode(), hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(computed, x_slack_signature):
            raise HTTPException(status_code=401, detail="Invalid Slack signature")
        slack_verified = True

    team_id = body.get("team_id", "")
    db = getattr(request.app.state, "db", None)
    tenant_id = await _resolve_tenant_from_channel("slack", team_id, db)

    gateway = _get_gateway(request)
    if gateway and tenant_id:
        await gateway.ingest("slack", body, tenant_id=tenant_id)
    elif not tenant_id:
        _log.warning("slack_event_unknown_team team_id=%s", team_id)

    if tenant_id:
        await _emit_chat_event(
            request,
            "slack",
            body,
            tenant_id,
            verified=_channel_verified(request, "slack", slack_ok=slack_verified),
        )
    return {"ok": True}


# ── Microsoft Teams ───────────────────────────────────────────────────────────


@router.post("/teams/events")
async def teams_events(request: Request) -> dict:
    """Handle Microsoft Teams webhook."""
    body = await request.json()
    tenant_id_from_header = request.headers.get("X-Tenant-ID", "")
    db = getattr(request.app.state, "db", None)
    tenant_id = tenant_id_from_header or await _resolve_tenant_from_channel(
        "teams", body.get("serviceUrl", ""), db
    )
    gateway = _get_gateway(request)
    if gateway and tenant_id:
        await gateway.ingest("teams", body, tenant_id=tenant_id)
    if tenant_id:
        await _emit_chat_event(
            request, "teams", body, tenant_id, verified=_channel_verified(request, "teams")
        )
    return {"type": "message", "text": "Received"}


# ── Discord ───────────────────────────────────────────────────────────────────


@router.post("/discord/events")
async def discord_events(request: Request) -> dict:
    """Handle Discord Interactions webhook."""
    body = await request.json()
    if body.get("type") == 1:  # PING
        return {"type": 1}
    guild_id = str(body.get("guild_id", ""))
    db = getattr(request.app.state, "db", None)
    tenant_id = await _resolve_tenant_from_channel("discord", guild_id, db)
    gateway = _get_gateway(request)
    if gateway and tenant_id:
        await gateway.ingest("discord", body, tenant_id=tenant_id)
    if tenant_id:
        await _emit_chat_event(
            request, "discord", body, tenant_id, verified=_channel_verified(request, "discord")
        )
    return {"type": 5}


# ── Email ─────────────────────────────────────────────────────────────────────


@router.post("/email/inbound")
async def email_inbound(request: Request) -> dict:
    """Handle SendGrid Inbound Parse webhook."""
    form = await request.form()
    to_email = str(form.get("to", ""))
    db = getattr(request.app.state, "db", None)
    tenant_id = await _resolve_tenant_from_channel("email", to_email, db)

    body = {
        "from": str(form.get("from", "")),
        "to": to_email,
        "subject": str(form.get("subject", "")),
        "text": str(form.get("text", "")),
    }
    gateway = _get_gateway(request)
    if gateway and tenant_id:
        await gateway.ingest("email", body, tenant_id=tenant_id)
    if tenant_id:
        await _emit_chat_event(
            request, "email", body, tenant_id, verified=_channel_verified(request, "email")
        )
    return {"status": "ok"}


# ── SMS (Twilio) ──────────────────────────────────────────────────────────────


@router.post("/sms/inbound")
async def sms_inbound(request: Request) -> str:
    """Handle Twilio SMS webhook."""
    form = await request.form()
    to_number = str(form.get("To", ""))
    db = getattr(request.app.state, "db", None)
    tenant_id = await _resolve_tenant_from_channel("sms", to_number, db)

    body = {
        "from": str(form.get("From", "")),
        "to": to_number,
        "body": str(form.get("Body", "")),
        "message_sid": str(form.get("MessageSid", "")),
    }
    gateway = _get_gateway(request)
    if gateway and tenant_id:
        await gateway.ingest("sms", body, tenant_id=tenant_id)
    if tenant_id:
        await _emit_chat_event(
            request, "sms", body, tenant_id, verified=_channel_verified(request, "sms")
        )
    return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


# ── Voice transcript ──────────────────────────────────────────────────────────


@router.post("/voice/transcript")
async def voice_transcript(request: Request) -> dict:
    """Handle voice transcript webhook (Twilio, Deepgram, etc.)."""
    body = await request.json()
    tenant_id = request.headers.get("X-Tenant-ID", "")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="X-Tenant-ID header required")
    gateway = _get_gateway(request)
    if gateway:
        await gateway.ingest("voice", body, tenant_id=tenant_id)
    await _emit_chat_event(
        request, "voice", body, tenant_id, verified=_channel_verified(request, "voice")
    )
    return {"status": "ok"}


# ── Forms ─────────────────────────────────────────────────────────────────────


@router.post("/forms/{form_id}")
async def form_submission(form_id: str, request: Request) -> dict:
    """Handle form submission webhook."""
    body = await request.json()
    tenant_id = request.headers.get("X-Tenant-ID", "")
    db = getattr(request.app.state, "db", None)
    if not tenant_id:
        tenant_id = await _resolve_tenant_from_channel("form", form_id, db) or ""
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Unable to resolve tenant from form_id")
    body["form_id"] = form_id
    gateway = _get_gateway(request)
    if gateway:
        await gateway.ingest("form", body, tenant_id=tenant_id)
    await _emit_chat_event(
        request, "form", body, tenant_id, verified=_channel_verified(request, "form")
    )
    return {"status": "ok"}


# ── Meeting ended ─────────────────────────────────────────────────────────────


@router.post("/meeting/ended")
async def meeting_ended(request: Request) -> dict:
    """Handle a meeting-ended webhook (Zoom / Teams / Google Meet)."""
    body = await request.json()
    tenant_id = request.headers.get("X-Tenant-ID", "")
    db = getattr(request.app.state, "db", None)
    if not tenant_id:
        tenant_id = (
            await _resolve_tenant_from_channel("meeting", body.get("account_id", ""), db) or ""
        )
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Unable to resolve tenant")
    gateway = _get_gateway(request)
    if gateway:
        await gateway.ingest("meeting", body, tenant_id=tenant_id)
    await _emit_chat_event(
        request, "meeting", body, tenant_id, verified=_channel_verified(request, "meeting")
    )
    return {"status": "ok"}


# ── Channel mappings CRUD ─────────────────────────────────────────────────────


@router.post("/mappings")
async def create_channel_mapping(request: Request) -> dict:
    """Register a channel (Slack workspace, Teams, etc.) to a tenant."""
    body = await request.json()
    tenant_id = getattr(getattr(request.state, "tenant", None), "tenant_id", "")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = getattr(request.app.state, "db", None)
    if db is None:
        # In-memory fallback
        return {
            "status": "mapped",
            "channel_type": body.get("channel_type"),
            "channel_id": body.get("channel_id"),
        }
    try:
        import uuid

        from sqlalchemy import text

        async with db() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO channel_tenant_mappings (id, tenant_id, channel_type, channel_id) "
                    "VALUES (:id, :tid, :ct, :ci) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "tid": tenant_id,
                    "ct": body.get("channel_type"),
                    "ci": body.get("channel_id"),
                },
            )
        return {"status": "mapped"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/mappings")
async def list_channel_mappings(request: Request) -> list[dict]:
    """List channel mappings for the tenant."""
    tenant_id = getattr(getattr(request.state, "tenant", None), "tenant_id", "")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = getattr(request.app.state, "db", None)
    if db is None:
        return []
    try:
        from sqlalchemy import text

        async with db() as session:
            rows = await session.execute(
                text(
                    "SELECT id, channel_type, channel_id, created_at FROM channel_tenant_mappings WHERE tenant_id = :tid"  # noqa: E501
                ),
                {"tid": tenant_id},
            )
            return [dict(r._mapping) for r in rows]
    except Exception:
        return []
