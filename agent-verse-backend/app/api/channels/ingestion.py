"""Channel Ingestion API — routes for Slack, Teams, Discord, email, SMS, voice, forms.

The inbound webhook routes (everything except ``/channels/mappings``) are called
by third parties that cannot send an AgentVerse API key, so they are exempt from
TenantMiddleware and MUST authenticate the caller themselves, before touching
any tenant data. Each one fails closed:

* ``slack``   — Slack signing-secret HMAC + 5-minute timestamp window
  (``app.state.slack_signing_secret`` or ``SLACK_SIGNING_SECRET``).
* ``teams``   — Bot Framework JWT for the platform bot (``TEAMS_APP_ID``) or the
  bound tenant's own bot app; the org must be bound (403 otherwise).
* ``discord`` — Ed25519 interaction signature (``DISCORD_PUBLIC_KEY``).
* ``email`` / ``sms`` / ``voice`` / ``form`` / ``meeting`` — an operator shared
  secret per channel (``app.state.channel_webhook_secrets[channel]`` or
  ``CHANNEL_WEBHOOK_SECRET_<CHANNEL>``), presented as ``X-Webhook-Secret`` or as
  the HTTP Basic password (SendGrid / Twilio put credentials in the URL).

Unconfigured → 503, bad or missing credential → 401.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["channels"])

# Slack rejects (and recommends rejecting) requests older than five minutes so a
# captured, correctly-signed request cannot be replayed later.
_SLACK_MAX_SKEW_SECONDS = 300


# ── Dependency helpers ────────────────────────────────────────────────────────


def _get_gateway(request: Request) -> Any:
    return getattr(request.app.state, "channel_gateway", None)


def _get_store(request: Request) -> Any:
    return getattr(request.app.state, "schedule_store", None)


def _get_dispatcher(request: Request) -> Any:
    return getattr(request.app.state, "trigger_dispatcher", None)


def _lookup_db(request: Request) -> Any:
    """Session factory for the pre-auth channel → tenant lookup.

    The caller is not a tenant yet, so the lookup is cross-tenant by nature and
    must use the maintenance (BYPASSRLS) factory; under the NOBYPASSRLS API role
    a GUC-less query sees zero rows. (It previously read ``app.state.db``, which
    production never sets, so no mapping ever resolved.) ``app.state.db`` is kept
    only as a fallback for tests that inject a fake factory there.
    """
    state = request.app.state
    return getattr(state, "system_db_session_factory", None) or getattr(state, "db", None)


def _tenant_db(request: Request) -> Any:
    """Tenant-scoped session factory for the mapping CRUD.

    The CRUD read ``app.state.db`` — never set in production — so creating a
    mapping silently took the in-memory "mapped" branch and nothing was ever
    stored, which is why inbound channel messages never resolved a tenant.
    ``app.state.db`` remains only as a fallback for tests that inject it.
    """
    state = request.app.state
    return getattr(state, "db_session_factory", None) or getattr(state, "db", None)


# ── Inbound authentication (fail closed) ─────────────────────────────────────


def _slack_signing_secret(request: Request) -> str:
    secret = str(getattr(request.app.state, "slack_signing_secret", "") or "")
    return secret or os.getenv("SLACK_SIGNING_SECRET", "")


def _require_slack_signature(request: Request, body: bytes, signature: str, ts: str) -> None:
    """Old behaviour verified only when a secret was configured AND the request
    carried ``X-Slack-Signature`` — dropping the header skipped verification."""
    secret = _slack_signing_secret(request)
    if not secret:
        raise HTTPException(503, "Slack channel is not configured (SLACK_SIGNING_SECRET unset)")
    if not signature or not ts:
        raise HTTPException(401, "Missing Slack signature")
    try:
        skew = abs(time.time() - int(ts))
    except ValueError:
        raise HTTPException(401, "Invalid Slack timestamp") from None
    if skew > _SLACK_MAX_SKEW_SECONDS:
        raise HTTPException(401, "Stale Slack request")
    base = b"v0:" + ts.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Invalid Slack signature")


def _channel_secret(request: Request, channel_type: str) -> str:
    secrets = getattr(request.app.state, "channel_webhook_secrets", None) or {}
    configured = str(secrets.get(channel_type, "") or "")
    return configured or os.getenv(f"CHANNEL_WEBHOOK_SECRET_{channel_type.upper()}", "")


def _basic_auth_password(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("basic "):
        return ""
    try:
        decoded = base64.b64decode(auth[6:].strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return ""
    return decoded.partition(":")[2]


def _require_channel_secret(request: Request, channel_type: str) -> None:
    """Shared-secret auth for relay-style channels. These endpoints used to verify
    nothing and took the tenant from ``X-Tenant-ID``, so anyone could inject
    messages (and fire conversational triggers) into any tenant."""
    expected = _channel_secret(request, channel_type)
    if not expected:
        raise HTTPException(
            503,
            f"{channel_type} channel is not configured "
            f"(CHANNEL_WEBHOOK_SECRET_{channel_type.upper()} unset)",
        )
    presented = request.headers.get("X-Webhook-Secret", "") or _basic_auth_password(request)
    if not presented or not hmac.compare_digest(expected, presented):
        raise HTTPException(401, f"Invalid {channel_type} webhook credentials")


async def _require_adapter_auth(adapter: Any, request: Request, body: bytes) -> None:
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        payload = json.loads(body) if body else {}
    except ValueError:
        payload = {}
    if not await adapter.verify_auth(headers, payload, body):
        raise HTTPException(401, "Invalid channel signature")


def _relay_tenant(request: Request) -> str:
    """``X-Tenant-ID`` is honoured only on relay channels and only AFTER
    :func:`_require_channel_secret` passed — the secret is operator-level, so its
    holder is the operator's own relay (same model as GATEWAY_INGRESS_SECRET)."""
    return (request.headers.get("X-Tenant-ID", "") or "").strip()


def _parse_json(body: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(body) if body else {}
    except ValueError:
        raise HTTPException(400, "Body must be JSON") from None
    if not isinstance(parsed, dict):
        raise HTTPException(400, "Body must be a JSON object")
    return parsed


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
    """Look up tenant_id from channel_tenant_mappings (cross-tenant, system session).

    Only a routable mapping (verified, or legacy pre-TRG-03) resolves; a pending
    claim has not proven it owns the channel and routes nothing. At most one
    routable mapping exists per channel (partial unique index).
    """
    if db is None or not channel_id:
        return None
    try:
        from sqlalchemy import text

        from app.api.channels.verification import STATUS_LEGACY, STATUS_VERIFIED
        from app.db.rls import system_session

        async with db() as session, session.begin(), system_session(session):
            row = await session.execute(
                text(
                    "SELECT tenant_id FROM channel_tenant_mappings "
                    "WHERE channel_type = :ct AND channel_id = :ci "
                    "AND status IN (:verified, :legacy) LIMIT 1"
                ),
                {
                    "ct": channel_type,
                    "ci": channel_id,
                    "verified": STATUS_VERIFIED,
                    "legacy": STATUS_LEGACY,
                },
            )
            result = row.fetchone()
            return str(result[0]) if result else None
    except Exception as exc:
        _log.warning("channel_tenant_lookup_failed channel=%s: %s", channel_type, exc)
        return None


async def _consume_ownership_proof(
    request: Request, channel_type: str, channel_id: str, payload: Any
) -> bool:
    """TRG-03: True when this authenticated inbound message carried a pending
    claim's one-time code for THIS channel and verified it. Such a message is the
    proof itself, so the caller acknowledges it without routing it anywhere.
    Messages without a code cost no DB round trip."""
    from app.api.channels import verification

    verified_tenant = await verification.verify_from_inbound(
        _lookup_db(request), channel_type, channel_id, payload
    )
    return verified_tenant is not None


# ── Slack Events API ──────────────────────────────────────────────────────────


@router.post("/slack/events")
async def slack_events(
    request: Request,
    x_slack_signature: str = Header(default=""),
    x_slack_request_timestamp: str = Header(default=""),
) -> dict:
    """Handle Slack Events API webhook."""
    body_bytes = await request.body()
    # Verify BEFORE anything else — including the url_verification challenge,
    # which Slack signs too.
    _require_slack_signature(request, body_bytes, x_slack_signature, x_slack_request_timestamp)
    body = _parse_json(body_bytes)

    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    team_id = str(body.get("team_id", "") or "")
    if await _consume_ownership_proof(request, "slack", team_id, body):
        return {"ok": True}
    tenant_id = await _resolve_tenant_from_channel("slack", team_id, _lookup_db(request))

    gateway = _get_gateway(request)
    if gateway and tenant_id:
        await gateway.ingest("slack", body, tenant_id=tenant_id)
    elif not tenant_id:
        _log.warning("slack_event_unknown_team team_id=%s", team_id)

    if tenant_id:
        await _emit_chat_event(request, "slack", body, tenant_id, verified=True)
    return {"ok": True}


# ── Microsoft Teams ───────────────────────────────────────────────────────────


def _normalize_m365_tenant_id(value: Any) -> str:
    """Canonical (lower-case, hyphenated) Microsoft 365 tenant GUID, or ``""``."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        return ""


def _teams_org_id(body: dict[str, Any]) -> str:
    """The Microsoft 365 tenant (organisation) id of a Bot Framework activity.

    Teams stamps it on ``channelData.tenant.id`` and ``conversation.tenantId``.
    ``serviceUrl`` is deliberately NOT used: it is a regional Bot Framework
    endpoint (``https://smba.trafficmanager.net/<region>/``) shared by every
    Teams organisation, so mapping it routed every org's messages to whichever
    AgentVerse tenant claimed the URL first.
    """
    channel_data = body.get("channelData")
    if isinstance(channel_data, dict):
        tenant = channel_data.get("tenant")
        if isinstance(tenant, dict):
            org = _normalize_m365_tenant_id(tenant.get("id"))
            if org:
                return org
    conversation = body.get("conversation")
    if isinstance(conversation, dict):
        return _normalize_m365_tenant_id(conversation.get("tenantId"))
    return ""


class _ChannelLookupUnavailableError(RuntimeError):
    """The channel → tenant table could not be read (answer 503, never guess)."""


async def _teams_binding(request: Request, org_id: str) -> tuple[str, str] | None:
    """``(tenant_id, bound_app_id)`` of the routable mapping for a Microsoft 365
    org, or None when the org is not bound. A DB error raises (the caller
    answers 503 so Bot Framework retries) instead of reading as "unbound".

    ``bound_app_id`` is the tenant's own Bot Framework app (a TRG-42 gateway
    binding); "" when the org talks to the platform bot only.
    """
    db = _lookup_db(request)
    if db is None or not org_id:
        return None
    from sqlalchemy import text

    from app.api.channels.verification import STATUS_LEGACY, STATUS_VERIFIED
    from app.db.rls import system_session

    try:
        async with db() as session, session.begin(), system_session(session):
            row = (
                await session.execute(
                    text(
                        "SELECT tenant_id, channel_config FROM channel_tenant_mappings "
                        "WHERE channel_type = :ct AND channel_id = :ci "
                        "AND status IN (:verified, :legacy) LIMIT 1"
                    ),
                    {
                        "ct": "teams",
                        "ci": org_id,
                        "verified": STATUS_VERIFIED,
                        "legacy": STATUS_LEGACY,
                    },
                )
            ).fetchone()
    except Exception as exc:
        _log.warning("teams_binding_lookup_failed: %s", str(exc)[:200])
        raise _ChannelLookupUnavailableError(str(exc)[:200]) from exc
    if row is None:
        return None
    try:
        config = row[1]
    except (IndexError, KeyError):
        config = None
    app_id = str(config.get("app_id") or "") if isinstance(config, dict) else ""
    return str(row[0]), app_id


@router.post("/teams/events")
async def teams_events(request: Request) -> dict:
    """Handle Microsoft Teams webhook (Bot Framework JWT authenticated).

    DEF-1: the tenant is bound by what the VERIFIED token and activity say:

    1. The Bot Framework JWT is validated (RS256 against the cached Bot
       Framework JWKS, issuer, expiry, ``serviceurl`` claim = activity
       ``serviceUrl``, key endorsements) — 401 otherwise.
    2. Its audience (the bot app id) must be the platform bot
       (``TEAMS_APP_ID``) or the Bot Framework app the bound tenant registered
       for THIS Microsoft 365 org — a tenant's own bot cannot carry another
       org's activities into someone else's tenant (401).
    3. The org (``channelData.tenant.id`` / ``conversation.tenantId``) must be
       bound to a tenant by a verified mapping; unknown or unbound orgs get 403.
       ``serviceUrl`` is never used: it is a regional endpoint shared by every
       Teams organisation, and mapping it routed every org to one tenant.
    """
    from app.gateway.channels.teams import decode_bot_framework_token, token_audience

    body_bytes = await request.body()
    try:
        activity = json.loads(body_bytes) if body_bytes else {}
    except ValueError:
        activity = {}
    if not isinstance(activity, dict):
        activity = {}
    headers = {k.lower(): v for k, v in request.headers.items()}
    claims = await decode_bot_framework_token(headers, activity)
    if claims is None:
        raise HTTPException(401, "Invalid channel signature")
    audience = token_audience(claims)
    platform_app = os.getenv("TEAMS_APP_ID", "").strip()
    body = _parse_json(body_bytes)
    org_id = _teams_org_id(body)

    via_platform = bool(platform_app) and audience == platform_app
    try:
        binding = await _teams_binding(request, org_id)
    except _ChannelLookupUnavailableError:
        raise HTTPException(503, "Channel mappings are temporarily unavailable") from None
    bound_app = binding[1] if binding else ""
    if not via_platform and not (bound_app and audience == bound_app):
        raise HTTPException(401, "Invalid channel signature")

    if via_platform and await _consume_ownership_proof(request, "teams", org_id, body):
        return {"type": "message", "text": "Channel verified"}
    if binding is None:
        _log.warning("teams_event_unbound_org org_id=%s", org_id or "<missing>")
        raise HTTPException(403, "This Microsoft 365 organisation is not bound to a tenant")
    tenant_id = binding[0]
    gateway = _get_gateway(request)
    if gateway:
        await gateway.ingest("teams", body, tenant_id=tenant_id)
    await _emit_chat_event(request, "teams", body, tenant_id, verified=True)
    return {"type": "message", "text": "Received"}


# ── Discord ───────────────────────────────────────────────────────────────────


@router.post("/discord/events")
async def discord_events(request: Request) -> dict:
    """Handle Discord Interactions webhook (Ed25519 signature authenticated)."""
    from app.gateway.channels.discord import DiscordChannelAdapter

    body_bytes = await request.body()
    # Discord itself requires a 401 for bad signatures (it probes with one), and
    # the PING is signed too — so verify before answering it.
    await _require_adapter_auth(DiscordChannelAdapter(), request, body_bytes)
    body = _parse_json(body_bytes)
    if body.get("type") == 1:  # PING
        return {"type": 1}
    guild_id = str(body.get("guild_id", ""))
    if await _consume_ownership_proof(request, "discord", guild_id, body):
        return {"type": 4, "data": {"content": "Channel verified", "flags": 64}}
    tenant_id = await _resolve_tenant_from_channel("discord", guild_id, _lookup_db(request))
    gateway = _get_gateway(request)
    if gateway and tenant_id:
        await gateway.ingest("discord", body, tenant_id=tenant_id)
    if tenant_id:
        await _emit_chat_event(request, "discord", body, tenant_id, verified=True)
    return {"type": 5}


# ── Email ─────────────────────────────────────────────────────────────────────


@router.post("/email/inbound")
async def email_inbound(request: Request) -> dict:
    """Handle SendGrid Inbound Parse webhook."""
    _require_channel_secret(request, "email")
    form = await request.form()
    to_email = str(form.get("to", ""))
    body = {
        "from": str(form.get("from", "")),
        "to": to_email,
        "subject": str(form.get("subject", "")),
        "text": str(form.get("text", "")),
    }
    if await _consume_ownership_proof(
        request, "email", to_email, {"subject": body["subject"], "text": body["text"]}
    ):
        return {"status": "ok"}
    tenant_id = _relay_tenant(request) or await _resolve_tenant_from_channel(
        "email", to_email, _lookup_db(request)
    )
    gateway = _get_gateway(request)
    if gateway and tenant_id:
        await gateway.ingest("email", body, tenant_id=tenant_id)
    if tenant_id:
        await _emit_chat_event(request, "email", body, tenant_id, verified=True)
    return {"status": "ok"}


# ── SMS (Twilio) ──────────────────────────────────────────────────────────────


_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@router.post("/sms/inbound")
async def sms_inbound(request: Request) -> Response:
    """Handle Twilio SMS webhook.

    Previously this verified nothing: any caller able to reach it could post an
    "SMS" whose ``To`` maps to another tenant and have it ingested into that
    tenant's gateway. It now requires a valid ``X-Twilio-Signature`` (fail closed:
    503 when ``TWILIO_AUTH_TOKEN`` is unset, 403 when invalid), and honours the
    STOP / START keywords in the telephony consent ledger — a STOP used to be
    ingested as an ordinary message for the agent to act on. The Twilio
    signature is this channel's credential (Twilio cannot send the generic
    channel secret), and the tenant comes only from the ``To`` number mapping.
    """
    import os

    from app.gateway.telephony_consent import (
        PLATFORM_TENANT,
        get_telephony_consent_ledger,
    )
    from app.gateway.twilio_auth import require_twilio_signature

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    require_twilio_signature(
        (os.getenv("TWILIO_AUTH_TOKEN") or "").strip(),
        url=str(request.url),
        params=params,
        signature=request.headers.get("X-Twilio-Signature", ""),
        label="Twilio SMS",
        env_var="TWILIO_AUTH_TOKEN",
    )

    to_number = params.get("To", "")
    from_number = params.get("From", "")
    if await _consume_ownership_proof(request, "sms", to_number, params.get("Body", "")):
        return Response(content=_EMPTY_TWIML, media_type="application/xml")
    tenant_id = await _resolve_tenant_from_channel("sms", to_number, _lookup_db(request))

    ledger = (
        getattr(request.app.state, "telephony_consent_ledger", None)
        or get_telephony_consent_ledger()
    )
    keyword = ledger.apply_inbound_keyword(
        tenant_id or PLATFORM_TENANT, from_number, params.get("Body", "")
    )
    if keyword is not None:
        _log.info("sms_consent_keyword keyword=%s tenant=%s", keyword, tenant_id or "platform")
        return Response(content=_EMPTY_TWIML, media_type="application/xml")

    body = {
        "from": from_number,
        "to": to_number,
        "body": params.get("Body", ""),
        "message_sid": params.get("MessageSid", ""),
    }
    gateway = _get_gateway(request)
    if gateway and tenant_id:
        await gateway.ingest("sms", body, tenant_id=tenant_id)
    if tenant_id:
        await _emit_chat_event(request, "sms", body, tenant_id, verified=True)
    # Twilio expects TwiML; returning a bare `str` made FastAPI JSON-encode it.
    return Response(content=_EMPTY_TWIML, media_type="application/xml")


# ── Voice transcript ──────────────────────────────────────────────────────────


@router.post("/voice/transcript")
async def voice_transcript(request: Request) -> dict:
    """Handle voice transcript webhook (Twilio, Deepgram, etc.)."""
    _require_channel_secret(request, "voice")
    body = _parse_json(await request.body())
    tenant_id = _relay_tenant(request)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="X-Tenant-ID header required")
    gateway = _get_gateway(request)
    if gateway:
        await gateway.ingest("voice", body, tenant_id=tenant_id)
    await _emit_chat_event(request, "voice", body, tenant_id, verified=True)
    return {"status": "ok"}


# ── Forms ─────────────────────────────────────────────────────────────────────


@router.post("/forms/{form_id}")
async def form_submission(form_id: str, request: Request) -> dict:
    """Handle form submission webhook."""
    _require_channel_secret(request, "form")
    body = _parse_json(await request.body())
    if await _consume_ownership_proof(request, "form", form_id, body):
        return {"status": "ok"}
    tenant_id = _relay_tenant(request) or (
        await _resolve_tenant_from_channel("form", form_id, _lookup_db(request)) or ""
    )
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Unable to resolve tenant from form_id")
    body["form_id"] = form_id
    gateway = _get_gateway(request)
    if gateway:
        await gateway.ingest("form", body, tenant_id=tenant_id)
    await _emit_chat_event(request, "form", body, tenant_id, verified=True)
    return {"status": "ok"}


# ── Meeting ended ─────────────────────────────────────────────────────────────


@router.post("/meeting/ended")
async def meeting_ended(request: Request) -> dict:
    """Handle a meeting-ended webhook (Zoom / Teams / Google Meet)."""
    _require_channel_secret(request, "meeting")
    body = _parse_json(await request.body())
    account_id = str(body.get("account_id", "") or "")
    if await _consume_ownership_proof(request, "meeting", account_id, body):
        return {"status": "ok"}
    tenant_id = _relay_tenant(request) or (
        await _resolve_tenant_from_channel("meeting", account_id, _lookup_db(request)) or ""
    )
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Unable to resolve tenant")
    gateway = _get_gateway(request)
    if gateway:
        await gateway.ingest("meeting", body, tenant_id=tenant_id)
    await _emit_chat_event(request, "meeting", body, tenant_id, verified=True)
    return {"status": "ok"}


# ── Channel mappings CRUD ─────────────────────────────────────────────────────


def _request_tenant(request: Request) -> str:
    tenant_id = getattr(getattr(request.state, "tenant", None), "tenant_id", "")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return str(tenant_id)


_CLAIMED_DETAIL = "Channel is already verified by another tenant"


@router.post("/mappings")
async def create_channel_mapping(request: Request) -> dict:
    """Claim a channel (Slack workspace, Teams org, Discord guild, number, address).

    TRG-03: the claim is created ``pending_verification`` with a one-time code and
    routes nothing until an inbound message on that channel carries the code. A
    channel another tenant has verified is refused (409); re-posting an own claim
    issues a fresh code (or reports ``verified``).

    On channels anyone can send to (sms, email, form, meeting, voice) a code
    proves nothing, so the claim is ``pending_operator_approval`` instead: no
    code, no routing, until a platform operator approves it (``/admin``).
    """
    from app.api.channels import verification

    tenant_id = _request_tenant(request)
    body = await request.json()
    db = _tenant_db(request)
    if db is None:
        # Used to answer {"status": "mapped"} while storing nothing.
        raise HTTPException(status_code=503, detail="Channel mappings need the database")
    channel_type = str(body.get("channel_type") or "")
    channel_id = str(body.get("channel_id") or "")
    if not channel_type or not channel_id:
        raise HTTPException(status_code=422, detail="channel_type and channel_id are required")
    if channel_type == "teams":
        # Teams routes by Microsoft 365 tenant id; a serviceUrl is shared by
        # every Teams organisation and must not be claimable.
        channel_id = _normalize_m365_tenant_id(channel_id)
        if not channel_id:
            raise HTTPException(
                status_code=422,
                detail="For Teams, channel_id must be your Microsoft 365 tenant ID (a GUID)",
            )
    try:
        issued = await verification.claim_channel(
            tenant_db=db,
            system_db=_lookup_db(request),
            tenant_id=tenant_id,
            channel_type=channel_type,
            channel_id=channel_id,
        )
    except verification.ChannelClaimedError:
        raise HTTPException(status_code=409, detail=_CLAIMED_DETAIL) from None
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return issued.to_response()


@router.post("/mappings/{mapping_id}/verify")
async def verify_channel_mapping(mapping_id: str, request: Request) -> dict:
    """Issue a fresh one-time code for a pending or legacy mapping ("Verify" in the UI).

    A legacy mapping keeps routing while its code is outstanding. Send-only
    channels (sms, email, ...) cannot be verified by code: 409, awaiting operator
    approval.
    """
    from app.api.channels import verification

    tenant_id = _request_tenant(request)
    db = _tenant_db(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Channel mappings need the database")
    try:
        issued = await verification.reissue_code(
            tenant_db=db,
            system_db=_lookup_db(request),
            tenant_id=tenant_id,
            mapping_id=mapping_id,
        )
    except verification.MappingNotFoundError:
        raise HTTPException(status_code=404, detail="Channel mapping not found") from None
    except verification.ChannelClaimedError:
        raise HTTPException(status_code=409, detail=_CLAIMED_DETAIL) from None
    except verification.OperatorApprovalRequiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return issued.to_response()


@router.get("/mappings")
async def list_channel_mappings(request: Request, status: str | None = None) -> list[dict]:
    """List the tenant's channel mappings with their verification status.

    ``?status=legacy_unverified`` lists the pre-TRG-03 mappings still awaiting proof.
    """
    from app.api.channels import verification

    tenant_id = _request_tenant(request)
    if status is not None and status not in verification.ALL_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {', '.join(verification.ALL_STATUSES)}",
        )
    db = _tenant_db(request)
    if db is None:
        return []
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        sql = (
            "SELECT id, channel_type, channel_id, status, verified_at, "
            "verification_expires_at, created_at FROM channel_tenant_mappings "
            "WHERE tenant_id = :tid"
        )
        params: dict[str, Any] = {"tid": tenant_id}
        if status is not None:
            sql += " AND status = :status"
            params["status"] = status
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            rows = await session.execute(text(sql + " ORDER BY created_at"), params)
            return [_with_remap_flag(dict(r._mapping)) for r in rows]
    except Exception:
        return []


def _with_remap_flag(row: dict[str, Any]) -> dict[str, Any]:
    """Flag legacy Teams mappings keyed on serviceUrl — they no longer route."""
    row["needs_remapping"] = row.get("channel_type") == "teams" and not (
        _normalize_m365_tenant_id(row.get("channel_id"))
    )
    return row
