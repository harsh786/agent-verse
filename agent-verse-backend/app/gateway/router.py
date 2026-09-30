"""Gateway router — FastAPI endpoints for all channels.

New endpoints:
  POST /v1/gateway/{org_id}/telegram/webhook
  POST /v1/gateway/{org_id}/slack/events
  GET  /v1/gateway/{org_id}/slack/events  (URL verification)
  POST /v1/gateway/{org_id}/whatsapp/webhook
  GET  /v1/gateway/{org_id}/whatsapp/webhook  (verification)
  POST /v1/gateway/{org_id}/teams/messages
  POST /v1/gateway/{org_id}/webhook
  POST /v1/gateway/voice/incoming  (Twilio call webhook, signature required)
  POST /v1/gateway/{channel}/chat
  GET  /v1/gateway/{org_id}/config           (501 — not implemented)
  PUT  /v1/gateway/{org_id}/config           (501 — not implemented)
  GET  /v1/gateway/{org_id}/channels/status  (501 — not implemented)
"""

from __future__ import annotations

import re
from contextlib import suppress
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from fastapi.responses import Response
from opentelemetry import trace
from pydantic import BaseModel

from app.gateway.channels.slack import SlackChannelAdapter
from app.gateway.channels.teams import MicrosoftTeamsAdapter
from app.gateway.channels.telegram import TelegramChannelAdapter
from app.gateway.channels.voice_phone import VoicePhoneChannelAdapter
from app.gateway.channels.webhook import WebhookChannelAdapter
from app.gateway.channels.whatsapp import WhatsAppChannelAdapter
from app.gateway.command import OrgCommand, OrgResponse
from app.gateway.dedup_scheduler import CommandDeduplicator

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

router = APIRouter(prefix="/v1/gateway", tags=["gateway"])

# ── Channel adapter singletons ────────────────────────────────────────────────
_telegram = TelegramChannelAdapter()
_slack = SlackChannelAdapter()
_whatsapp = WhatsAppChannelAdapter()
_teams = MicrosoftTeamsAdapter()
_webhook = WebhookChannelAdapter()
_voice_phone = VoicePhoneChannelAdapter()


async def _authenticate_channel(
    adapter: Any,
    channel: str,
    headers: dict[str, str],
    raw: dict[str, Any],
    raw_body: bytes | None = None,
) -> None:
    """Fail-closed channel-secret check for the public (auth-bypassed) webhooks.

    These routes sit in TenantMiddleware's bypass list, so the channel secret is
    their ONLY authentication. Telegram / WhatsApp / Slack / generic used to
    accept every request when their secret was unset. Now: unconfigured → 503,
    bad/missing credential (or a verifier error) → 401 — the same contract as
    app/api/channels/ingestion.py.
    """
    if not getattr(adapter, "is_configured", False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{channel} webhook is not configured (no signing secret set)",
        )
    try:
        verified = await adapter.verify_auth(headers, raw, raw_body=raw_body)
    except Exception as exc:
        _log.warning("gateway.channel_auth_error", channel=channel, error=str(exc)[:160])
        verified = False
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid {channel} webhook credentials",
        )


def _verify_binding_secret(
    channel: str, secret: str, headers: dict[str, str], raw_body: bytes
) -> bool:
    """Authenticate an inbound chat message against a binding's own secret.

    Same wire formats as the platform adapters: Telegram's static
    ``X-Telegram-Bot-Api-Secret-Token``; WhatsApp's ``X-Hub-Signature-256`` and
    the generic ``X-Webhook-Signature`` HMAC-SHA256 over the raw body.
    """
    import hashlib
    import hmac

    if not secret:
        return False
    if channel == "telegram":
        presented = headers.get("x-telegram-bot-api-secret-token", "")
        return bool(presented) and hmac.compare_digest(secret.encode(), presented.encode())
    header = {"whatsapp": "x-hub-signature-256", "webhook": "x-webhook-signature"}.get(channel)
    if header is None:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, headers.get(header, ""))


# ── Schemas ───────────────────────────────────────────────────────────────────


class CommandStreamResponse(BaseModel):
    command_id: str
    status: str = "processing"
    estimated_response_ms: int = 2000


class GatewayConfig(BaseModel):
    telegram_enabled: bool = False
    slack_enabled: bool = False
    whatsapp_enabled: bool = False
    teams_enabled: bool = False
    discord_enabled: bool = False
    email_enabled: bool = False
    mcp_enabled: bool = True
    webhook_enabled: bool = True
    max_commands_per_hour: int = 100


# ── Helpers ───────────────────────────────────────────────────────────────────


def trusted_gateway_tenant(headers: dict[str, str]) -> str:
    """Resolve the tenant a gateway webhook is allowed to act for.

    SECURITY: the channel webhooks are unauthenticated at the tenant layer (they
    are in _BYPASS_PREFIXES and rely on per-channel signature checks), so the
    ``x-tenant-id`` header is attacker-controllable. Acting on it directly is a
    cross-tenant bypass — any caller could create goals / ingest documents for an
    arbitrary tenant. We therefore trust ``x-tenant-id`` ONLY when the caller (the
    operator's own relay that forwards Telegram/WhatsApp updates) presents the
    shared ``GATEWAY_INGRESS_SECRET`` in ``x-gateway-secret``. With no secret
    configured, no tenant is trusted and the command is acknowledged but performs
    no tenant-scoped work — never a silent cross-tenant action.
    """
    import hmac
    import os

    secret = os.getenv("GATEWAY_INGRESS_SECRET", "")
    if not secret:
        return ""
    presented = headers.get("x-gateway-secret", "")
    if not presented or not hmac.compare_digest(secret, presented):
        return ""
    return (headers.get("x-tenant-id", "") or "").strip()


def _tenant_ctx_for(command: OrgCommand) -> Any:
    """Build a TenantContext from the command's already-trust-validated tenant_id
    (set by the webhook handler via :func:`trusted_gateway_tenant`). Empty when no
    tenant was trusted — callers must skip tenant-scoped work in that case."""
    from app.tenancy.context import PlanTier, TenantContext

    tenant_id = command.tenant_id
    return TenantContext(tenant_id=tenant_id, api_key_id="gateway", plan=PlanTier.FREE), tenant_id


_CREDENTIAL_QUERY_KEYS = frozenset(
    {
        "token",
        "access_token",
        "api_key",
        "apikey",
        "key",
        "secret",
        "sig",
        "signature",
        "password",
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-security-token",
        "se",
        "sp",
        "sv",
    }
)


def redact_url_credentials(url: str) -> str:
    """Strip embedded credentials from a file-download URL before persisting it.

    Chat channels hand out download URLs that carry the credential inline:
    Telegram's is ``https://api.telegram.org/file/bot<BOT_TOKEN>/<path>``,
    pre-signed object-store links put it in the query string, and a plain URL can
    put it in userinfo. That URL becomes ``RawDocument.source_url``, which is
    persisted on the document and copied into every chunk's metadata — and read
    back out as the ``source_url`` of a RAG citation. Persisting it unredacted
    writes the bot token to Postgres in plaintext and shows it to the tenant on
    every citation; rotating the token cannot un-write it.

    The URL is kept recognisable (host and path survive) because it is the
    document's provenance record.
    """
    if not url or "://" not in url:
        return url
    import re
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - defensive
        return "[redacted-url]"

    netloc = parts.netloc
    if "@" in netloc:
        netloc = "[redacted]@" + netloc.rsplit("@", 1)[1]

    # Telegram embeds the bot token as a path segment: /bot<token>/...
    path = re.sub(r"/bot[^/]+", "/bot[redacted]", parts.path)

    query = parts.query
    if query:
        query = urlencode(
            [
                (k, "[redacted]" if k.lower() in _CREDENTIAL_QUERY_KEYS else v)
                for k, v in parse_qsl(query, keep_blank_values=True)
            ]
        )

    return urlunsplit((parts.scheme, netloc, path, query, parts.fragment))


async def _download_command_file(cf: Any) -> bytes | None:
    if getattr(cf, "data", None):
        return cf.data
    url = getattr(cf, "url", None)
    if not url:
        return None
    # SSRF guard: file URLs arrive from external chat payloads, so they must be
    # confined to public hosts before any request (blocks localhost, link-local,
    # cloud metadata, and private ranges).
    from app.net.ssrf_guard import assert_public_url, public_async_client, request_public

    try:
        assert_public_url(str(url), context="gateway.file_download")
    except Exception as exc:
        _log.warning("gateway.file_url_blocked", error=str(exc)[:120])
        return None
    try:
        # Pinned to the address checked at connect time (a plain client
        # re-resolved the name: DNS rebinding); chat file links commonly
        # redirect to a CDN, so redirects are followed with every hop checked.
        async with public_async_client(timeout=20.0) as client:
            r = await request_public(client, "GET", str(url), context="gateway.file_download")
            r.raise_for_status()
            return r.content
    except Exception as exc:
        _log.warning("gateway.file_download_failed", error=str(exc)[:120])
        return None


async def _ensure_inbox_collection(state: Any, ctx: Any, channel: str) -> str | None:
    """Return the id of the per-tenant '{channel}-inbox' collection, creating it
    once so chat-delivered documents have a durable home in knowledge."""
    ks = getattr(state, "knowledge_store", None)
    if ks is None:
        return None
    name = f"{channel}-inbox"
    try:
        for coll in await ks.list_collections_async(tenant_ctx=ctx):
            if coll.name == name:
                return coll.collection_id
        from app.rag.models import KnowledgeCollection

        created = KnowledgeCollection(
            name=name, description=f"Documents received via {channel}", embedder="nvidia"
        )
        return await ks.create_collection_async(created, tenant_ctx=ctx)
    except Exception as exc:
        _log.warning("gateway.inbox_collection_failed", error=str(exc)[:120])
        return None


async def _ingest_command_files(command: OrgCommand, state: Any, ctx: Any, tenant_id: str) -> int:
    """Ingest each attached document into the tenant's chat inbox collection via
    the real ingestion pipeline (binary parse + OCR + chunk + embed)."""
    pipeline = getattr(state, "ingestion_pipeline", None)
    if pipeline is None or not command.files:
        return 0
    collection_id = await _ensure_inbox_collection(state, ctx, command.actor_channel)
    if not collection_id:
        return 0
    from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

    source = SourceConfig(
        source_id=f"chat-{command.command_id}",
        tenant_id=tenant_id,
        name=f"{command.actor_channel} chat",
        family=SourceFamily.COMMUNICATION,
        source_type="webhook",
        collection_id=collection_id,
        pii_action="allow",
    )
    indexed = 0
    for cf in command.files:
        data = await _download_command_file(cf)
        if not data:
            continue
        raw = RawDocument(
            doc_id=f"{command.command_id}-{getattr(cf, 'filename', 'file')}",
            source_id=source.source_id,
            tenant_id=tenant_id,
            content=data,
            content_type=getattr(cf, "content_type", "application/octet-stream"),
            title=getattr(cf, "filename", ""),
            # Never the raw URL: it carries the channel's credential (the
            # Telegram bot token is a path segment of it) and source_url is
            # persisted on the document, copied into chunk metadata, and echoed
            # back to the tenant as a RAG citation.
            source_url=redact_url_credentials(getattr(cf, "url", "") or ""),
        )
        try:
            result = await pipeline.ingest(raw, source)
            if result.status == "indexed":
                indexed += 1
        except Exception as exc:
            _log.warning("gateway.file_ingest_failed", error=str(exc)[:120])
    return indexed


async def _submit_goal_from_command(
    command: OrgCommand, state: Any, ctx: Any, text: str
) -> str | None:
    """Submit the message text as a natural-language goal — this is how an
    external chat 'command' actually drives the platform."""
    gs = getattr(state, "goal_service", None)
    if gs is None:
        return None
    try:
        result = await gs.submit_goal(
            goal=text,
            priority="normal" if command.urgency != "urgent" else "high",
            dry_run=False,
            tenant_ctx=ctx,
        )
        return result.get("goal_id") or result.get("id")
    except Exception as exc:
        _log.warning("gateway.goal_submit_failed", error=str(exc)[:160])
        return None


async def _reply_to_channel(command: OrgCommand, text: str) -> None:
    """Send an acknowledgement back to the originating chat, when the channel
    supports outbound messages and is configured with a token."""
    resp = OrgResponse(command_id=command.command_id, text=text, status="accepted")
    conv = command.conversation_id or command.actor_id or ""
    try:
        if command.actor_channel == "telegram" and conv:
            await _telegram.send_message(conv, resp)
        elif command.actor_channel == "whatsapp" and conv:
            await _whatsapp.send_message(conv, resp)
    except Exception as exc:
        _log.warning("gateway.reply_failed", error=str(exc)[:120])


async def _process_command(command: OrgCommand, state: Any = None) -> OrgResponse:
    """Turn an inbound chat command into real platform work: ingest any attached
    documents into knowledge, submit the text as a goal, and reply to the chat.

    ``state`` is the receiving app's ``request.app.state`` (passed by each route);
    it used to be read off the global ``app.main.app``, which is a different app
    from the one serving the request in multi-app setups and tests.
    """
    with _tracer.start_as_current_span("gateway.process_command") as span:
        span.set_attribute("channel", command.actor_channel)
        span.set_attribute("org_id", command.org_id)
        span.set_attribute("command_id", command.command_id)
        _log.info(
            "gateway.command_received",
            channel=command.actor_channel,
            org_id=command.org_id,
            has_files=bool(command.files),
            text_preview=command.text[:60],
        )

        ctx, tenant_id = _tenant_ctx_for(command)

        # No trusted tenant → acknowledge only. Never create goals or ingest
        # documents for a tenant derived from an unauthenticated, spoofable
        # header (see trusted_gateway_tenant).
        if not tenant_id:
            _log.warning(
                "gateway.untrusted_tenant_skipped",
                channel=command.actor_channel,
                org_id=command.org_id,
            )
            reply_text = "Received. (No tenant binding configured for this channel.)"
            await _reply_to_channel(command, reply_text)
            return OrgResponse(
                command_id=command.command_id,
                text=reply_text,
                status="accepted",
                requires_action=False,
            )

        parts: list[str] = []
        goal_id: str | None = None

        if state is not None and command.files:
            n = await _ingest_command_files(command, state, ctx, tenant_id)
            if n:
                parts.append(f"📎 Ingested {n} document(s) into '{command.actor_channel}-inbox'.")

        text = (command.text or "").strip()
        is_command = bool(text) and text != "(attachment)" and not text.startswith("/callback")
        if state is not None and is_command:
            goal_id = await _submit_goal_from_command(command, state, ctx, text)
            if goal_id:
                parts.append(
                    f"🎯 Started goal `{goal_id[:8]}` — I'll follow up here when it's done."
                )

        reply_text = "\n".join(parts) if parts else f"Received: {text[:120]}"
        span.set_attribute("goal_id", goal_id or "")
        await _reply_to_channel(command, reply_text)

        return OrgResponse(
            command_id=command.command_id,
            text=reply_text,
            status="accepted",
            mission_id=goal_id,
            requires_action=False,
        )


# ── Telegram ──────────────────────────────────────────────────────────────────


@router.post(
    "/{org_id}/telegram/webhook",
    operation_id="gateway_telegram_webhook",
    summary="Telegram Bot webhook receiver",
    status_code=status.HTTP_200_OK,
)
async def telegram_webhook(
    org_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, str]:
    raw = await request.json()
    headers = dict(request.headers)

    await _authenticate_channel(_telegram, "telegram", headers, raw)

    tenant_id = trusted_gateway_tenant(headers)  # spoof-proof: gated by ingress secret
    command = await _telegram.normalize(raw, tenant_id=tenant_id, org_id=org_id)

    if command.text:
        background_tasks.add_task(_process_command, command, request.app.state)

    return {"status": "ok"}


# ── Slack ─────────────────────────────────────────────────────────────────────


@router.get(
    "/{org_id}/slack/events",
    operation_id="gateway_slack_verify",
    summary="Slack URL verification challenge",
)
async def slack_verify(challenge: str = "") -> dict[str, str]:
    return {"challenge": challenge}


@router.post(
    "/{org_id}/slack/events",
    operation_id="gateway_slack_events",
    summary="Slack Events API receiver",
)
async def slack_events(
    org_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Any:
    raw_body = await request.body()
    raw = await request.json()
    headers = dict(request.headers)

    # URL verification challenge
    if raw.get("type") == "url_verification":
        return {"challenge": raw.get("challenge", "")}

    await _authenticate_channel(_slack, "slack", headers, raw, raw_body=raw_body)

    tenant_id = trusted_gateway_tenant(headers)  # spoof-proof: gated by ingress secret
    command = await _slack.normalize(raw, tenant_id=tenant_id, org_id=org_id)

    if command.text:
        background_tasks.add_task(_process_command, command, request.app.state)

    return {"status": "ok"}


# ── WhatsApp ──────────────────────────────────────────────────────────────────


@router.get(
    "/{org_id}/whatsapp/webhook",
    operation_id="gateway_whatsapp_verify",
    summary="WhatsApp webhook verification",
)
async def whatsapp_verify(
    hub_mode: str = "",
    hub_verify_token: str = "",
    hub_challenge: str = "",
) -> Any:
    import hmac
    import os

    # Fail closed: an unset WHATSAPP_VERIFY_TOKEN used to match an empty
    # hub_verify_token ("" == ""), letting anyone complete the subscription.
    expected = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="whatsapp webhook verification is not configured",
        )
    if hub_mode == "subscribe" and hmac.compare_digest(
        hub_verify_token.encode(), expected.encode()
    ):
        try:
            return int(hub_challenge)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid hub_challenge") from None
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post(
    "/{org_id}/whatsapp/webhook",
    operation_id="gateway_whatsapp_webhook",
    summary="WhatsApp Business Cloud API receiver",
)
async def whatsapp_webhook(
    org_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    raw_body = await request.body()
    raw = await request.json()
    headers = dict(request.headers)

    await _authenticate_channel(_whatsapp, "whatsapp", headers, raw, raw_body=raw_body)

    tenant_id = trusted_gateway_tenant(headers)  # spoof-proof: gated by ingress secret
    command = await _whatsapp.normalize(raw, tenant_id=tenant_id, org_id=org_id)

    if command.text:
        background_tasks.add_task(_process_command, command, request.app.state)

    return {"status": "ok"}


# ── Microsoft Teams ───────────────────────────────────────────────────────────


@router.post(
    "/{org_id}/teams/messages",
    operation_id="gateway_teams_messages",
    summary="Microsoft Teams Bot Framework receiver",
)
async def teams_messages(
    org_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    raw = await request.json()
    headers = dict(request.headers)

    if not await _teams.verify_auth(headers, raw):
        raise HTTPException(status_code=403, detail="Invalid Teams auth")

    tenant_id = trusted_gateway_tenant(headers)  # spoof-proof: gated by ingress secret
    command = await _teams.normalize(raw, tenant_id=tenant_id, org_id=org_id)

    if command.text:
        background_tasks.add_task(_process_command, command, request.app.state)

    return {"type": "message", "text": ""}


# ── Voice / phone (Twilio-style call webhook) ─────────────────────────────────


_VOICE_CONSENT_PROMPT = (
    "Hi, you're connected to an AI assistant. This call is transcribed and "
    "processed to answer you. Say yes to continue, or stop to end the call."
)
_AFFIRMATIVE = re.compile(r"^\W*(yes|yeah|yep|i agree|i consent|agree|consent)\b", re.I)


def _is_affirmative(text: str) -> bool:
    return bool(_AFFIRMATIVE.match(text))


def _is_opt_out(text: str) -> bool:
    from app.gateway.telephony_consent import OPT_OUT_KEYWORDS

    return text.strip().strip(".!").upper() in OPT_OUT_KEYWORDS


def _voice_consent_policy(state: Any) -> Any:
    """The app's fail-closed voice consent policy, created on first use.

    LIMITATION: VoiceConsentPolicy is process-local, so a caller who consented on
    one replica is asked again on another (fail closed, never open).
    """
    from app.voice.consent import VoiceConsentPolicy

    policy = getattr(state, "voice_consent_policy", None)
    if policy is None:
        policy = VoiceConsentPolicy(fail_closed=True)
        state.voice_consent_policy = policy
    return policy


@router.post(
    "/voice/incoming",
    operation_id="gateway_voice_incoming",
    summary="Inbound phone-call webhook (Twilio/Vonage) → ChatService, returns TwiML",
)
async def voice_incoming(request: Request) -> Response:
    """Handle one inbound call turn and speak a reply.

    Twilio posts form params (``From`` / ``To`` / ``CallSid`` / ``SpeechResult``).
    The tenant is resolved from the *called* number (``To``) via the voice registry
    (the webhook carries no API key). The caller's speech is routed through the
    SAME unified pipeline as web/WhatsApp (``ChatService.ahandle_channel_message``,
    channel ``voice_phone``, caller number as the channel user) so a call is a
    durable, identity-aware conversation. The reply is returned as TwiML the
    provider speaks, with a ``<Gather>`` that loops the next turn back here.
    """
    from app.voice.chat_bridge import handle_voice_turn
    from app.voice.retention import VoiceRetentionPolicy

    state = request.app.state
    adapter = getattr(state, "voice_phone_adapter", None) or _voice_phone
    registry = getattr(state, "voice_phone_registry", None)
    chat_service = getattr(state, "chat_service", None)

    form = dict((await request.form()).items())
    payload: dict[str, Any] = {**form, "_request_url": str(request.url)}

    # Provider signature verification — fail CLOSED. This was "open in dev when no
    # auth token configured" (verify_auth returned True) and an exception inside
    # verification surfaced as a 500, so an unconfigured deployment accepted
    # forged calls into any registered tenant. Mirrors app/integrations/
    # webhook_auth.py: unconfigured → 503, bad/missing signature or error → 403.
    if not getattr(adapter, "is_configured", False):
        raise HTTPException(
            status_code=503,
            detail="Voice webhook is not configured (VOICE_PHONE_AUTH_TOKEN is unset)",
        )
    try:
        verified = await adapter.verify_auth(dict(request.headers), payload)
    except Exception as exc:
        _log.warning("gateway.voice_signature_error", error=str(exc)[:160])
        verified = False
    if not verified:
        raise HTTPException(status_code=403, detail="Invalid voice signature")

    to_number = str(form.get("To", "") or "")
    from_number = str(form.get("From", "") or "")
    transcript = str(form.get("SpeechResult") or form.get("transcript") or "").strip()

    binding = registry.resolve(to_number) if registry is not None else None
    if binding is None or chat_service is None:
        # Unknown number / chat not wired — answer politely, do no tenant work.
        twiml = adapter.format_reply(
            "Sorry, this number isn't set up for the assistant yet. Goodbye.",
            gather=False,
        )["twiml"]
        return Response(content=twiml, media_type="application/xml")

    # Consent gate — fail CLOSED. `voice_consent_policy` was read from app.state
    # but nothing ever set it, so handle_voice_turn got None and processed and
    # persisted every caller's speech with no recorded consent. A fail-closed
    # policy now always exists; until the caller says "yes" to the spoken
    # notice, nothing they say is processed or stored.
    consent_policy = _voice_consent_policy(state)
    tenant_id = binding.tenant_id
    if transcript and _is_opt_out(transcript):
        consent_policy.revoke(tenant_id, from_number)
        twiml = adapter.format_reply(
            "Okay, I won't process this call. Goodbye.", gather=False
        )["twiml"]
        return Response(content=twiml, media_type="application/xml")
    if not consent_policy.has_consent(tenant_id, from_number):
        if transcript and _is_affirmative(transcript):
            consent_policy.record_consent(tenant_id, from_number, purpose="voice_phone")
            twiml = adapter.format_reply("Thank you. How can I help?")["twiml"]
        else:
            twiml = adapter.format_reply(_VOICE_CONSENT_PROMPT)["twiml"]
        return Response(content=twiml, media_type="application/xml")

    if not transcript:
        # Call connected but nothing said yet — greet and gather the first turn.
        twiml = adapter.format_reply("Hi, you're connected to your assistant. How can I help?")[
            "twiml"
        ]
        return Response(content=twiml, media_type="application/xml")

    try:
        result = await handle_voice_turn(
            chat_service=chat_service,
            tenant_id=tenant_id,
            caller_id=from_number,
            transcript=transcript,
            consent_policy=consent_policy,
            # A caller can speak an SSN / card number. app/voice/retention.py
            # documents PII redaction as the DEFAULT ("transcripts are
            # PII-redacted by default before they are kept or forwarded") and
            # the WebSocket path defaults it on the same way
            # (app/voice/streaming.py: `retention_policy or VoiceRetentionPolicy()`).
            # This phone path was the one entry point that passed nothing, so the
            # raw transcript was persisted verbatim into the durable chat session.
            retention_policy=(
                getattr(state, "voice_retention_policy", None) or VoiceRetentionPolicy()
            ),
        )
        reply = str(result.get("reply_text") or "Okay.")
    except Exception as exc:  # never drop the call — speak a safe fallback
        _log.warning("gateway.voice_turn_failed", error=str(exc)[:160])
        reply = "Sorry, I hit a problem handling that. Please try again."

    twiml = adapter.format_reply(reply)["twiml"]
    return Response(content=twiml, media_type="application/xml")


# ── Unified messaging chat (Telegram / WhatsApp → ChatService) ────────────────


def _external_message_id(channel: str, raw: dict[str, Any]) -> str:
    """Best-effort stable id for an inbound platform message.

    Telegram, WhatsApp and Slack all REDELIVER a webhook on any non-2xx or
    timeout, carrying the same id. Without it a redelivery is indistinguishable
    from a new message: the turn is persisted twice and, when the intent is
    GOAL, two goals are submitted for one user message. Returns "" when the
    payload has no usable id, in which case the deduplicator falls back to its
    time-bucket key.
    """
    try:
        if channel == "telegram":
            msg = raw.get("message") or raw.get("edited_message") or {}
            return str(msg.get("message_id") or raw.get("update_id") or "")
        if channel == "whatsapp":
            entry = (raw.get("entry") or [{}])[0]
            change = (entry.get("changes") or [{}])[0]
            messages = (change.get("value") or {}).get("messages") or [{}]
            return str(messages[0].get("id") or "")
        return str(
            raw.get("message_id")
            or raw.get("event_id")
            or raw.get("id")
            or ""
        )
    except Exception:  # a malformed payload must not break dispatch
        return ""


# Process-local fallback so redelivery protection works even before a Redis-backed
# deduplicator is wired onto app.state during lifespan.
_command_deduplicator = CommandDeduplicator()

_CHAT_ADAPTERS: dict[str, Any] = {
    "telegram": _telegram,
    "whatsapp": _whatsapp,
    "webhook": _webhook,
}


def _chat_addressee(channel: str, raw: dict[str, Any], binding_id: str) -> str:
    """Which binding a chat delivery is for (TRG-41).

    Real Telegram updates carry no bot id, so Telegram bindings are addressed by
    the webhook URL (``/{channel}/chat/{binding_id}``, registered with
    setWebhook). WhatsApp Cloud payloads name the receiving number in
    ``entry[].changes[].value.metadata.phone_number_id``. A top-level
    ``addressee`` / ``bot_id`` / ``to`` is still honoured for generic webhooks.
    Whatever is found only SELECTS a binding; the binding's secret authenticates.
    """
    if binding_id:
        return binding_id.strip()
    if channel == "whatsapp":
        with suppress(Exception):
            entry = (raw.get("entry") or [{}])[0]
            change = (entry.get("changes") or [{}])[0]
            metadata = (change.get("value") or {}).get("metadata") or {}
            phone_number_id = str(metadata.get("phone_number_id") or "")
            if phone_number_id:
                return phone_number_id
    return str(raw.get("addressee") or raw.get("bot_id") or raw.get("to") or "")


async def _send_chat_reply(
    channel: str, adapter: Any, binding: Any, addressee: str, chat_id: str, text: str
) -> bool:
    """Send the reply back with the binding's own outbound token."""
    token = getattr(binding, "outbound_token", "") if binding is not None else ""
    if not token or not chat_id or not text:
        return False
    result: Any = None
    if channel == "telegram":
        result = await adapter.send_text(chat_id=chat_id, text=text, token=token)
    elif channel == "whatsapp":
        result = await adapter.send_text(
            to=chat_id, text=text, token=token, phone_number_id=addressee
        )
    return result is not None


@router.post(
    "/{channel}/chat",
    operation_id="gateway_channel_chat",
    summary="Inbound Telegram/WhatsApp message → unified ChatService, replies back",
)
async def channel_chat(channel: str, request: Request) -> dict[str, Any]:
    """Route an inbound messaging webhook through the SAME ChatService as web chat.

    The tenant is resolved from the *addressee* (the bot/number that received the
    message) via the channel registry, so Telegram/WhatsApp share the durable,
    identity-aware chat brain (sessions, memory, cross-channel continuity). The
    reply is sent back over the channel's outbound API when a token is configured,
    and always returned in the response (so it is testable without live creds).
    """
    return await _channel_chat(channel, request, binding_id="")


@router.post(
    "/{channel}/chat/{binding_id}",
    operation_id="gateway_channel_chat_binding",
    summary="Inbound message for a specific binding (e.g. the Telegram bot id)",
)
async def channel_chat_for_binding(
    channel: str, binding_id: str, request: Request
) -> dict[str, Any]:
    """Same as ``/{channel}/chat`` with the binding named in the URL — the form a
    Telegram webhook must use, since Bot API updates do not name the bot."""
    return await _channel_chat(channel, request, binding_id=binding_id)


async def _channel_chat(channel: str, request: Request, *, binding_id: str) -> dict[str, Any]:
    channel = channel.strip().lower()
    adapter = _CHAT_ADAPTERS.get(channel)
    chat_service = getattr(request.app.state, "chat_service", None)
    registry = getattr(request.app.state, "channel_registry", None)
    if adapter is None or chat_service is None:
        raise HTTPException(status_code=404, detail=f"channel {channel!r} not available")

    raw_body = await request.body()
    raw = await request.json()
    headers = dict(request.headers)
    # whatsapp/webhook verify their HMAC against the raw body bytes (the parsed-
    # and-reserialized `raw` dict is never guaranteed byte-identical to what the
    # caller actually signed); telegram only checks a static secret header and
    # ignores raw_body entirely, so passing it here is a no-op for that adapter.
    #
    # SECURITY: the addressee (bot id / number) comes from the unauthenticated
    # body, so it may only SELECT a binding. The request is then authenticated
    # with that binding's per-tenant secret — never with the platform-wide
    # channel secret, which every tenant configuring a bot would share and could
    # therefore use to address any other tenant's bot.
    addressee = _chat_addressee(channel, raw, binding_id)
    binding = registry.resolve(channel, addressee) if registry is not None else None
    if binding is not None:
        if not binding.secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"{channel} binding has no per-tenant secret configured",
            )
        if not _verify_binding_secret(channel, binding.secret, headers, raw_body):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid {channel} webhook credentials",
            )
        tenant_id = binding.tenant_id
    else:
        # No binding: only the operator's own relay (platform channel secret +
        # GATEWAY_INGRESS_SECRET, tenant in a header) may name a tenant.
        await _authenticate_channel(adapter, channel, headers, raw, raw_body=raw_body)
        tenant_id = trusted_gateway_tenant(headers)
    if not tenant_id:
        # No tenant mapping — acknowledge without doing tenant-scoped work.
        return {"status": "ignored", "reason": "no tenant mapping for addressee"}

    _org = binding.org_id if binding else ""
    command = await adapter.normalize(raw, tenant_id=tenant_id, org_id=_org)
    if not command.text:
        return {"status": "ok", "reason": "no text in message"}

    # Drop a redelivered webhook. `CommandDeduplicator` existed for exactly this
    # ("Critical for: button double-taps, network retries, webhook replay") but
    # was wired into nothing, so a Telegram/WhatsApp retry persisted the turn
    # twice and submitted a second goal for one user message.
    #
    # Only when the platform gave us a real message id. The deduplicator's
    # fallback key is a 30-second bucket over (tenant, channel, actor, text),
    # which would also swallow a user legitimately sending the same short reply
    # ("yes", "ok") twice in half a minute. A redelivery always carries the
    # original id, so keying strictly on it fixes the replay without inventing a
    # false positive.
    _external_id = _external_message_id(channel, raw)
    if _external_id:
        _dedup = (
            getattr(request.app.state, "command_deduplicator", None)
            or _command_deduplicator
        )
        if not await _dedup.check_and_reserve(
            command.command_id,
            tenant_id,
            _org,
            channel,
            command.conversation_id or command.actor_id,
            command.text,
            external_id=_external_id,
        ):
            return {"status": "ok", "reason": "duplicate message ignored"}

    turn = await chat_service.achannel_turn(
        tenant_id=tenant_id, channel=channel,
        channel_user_id=command.conversation_id or command.actor_id, text=command.text,
    )

    # Send the reply back over the channel with the binding's outbound token. The
    # old call passed keywords no adapter accepted, so it raised (suppressed) and
    # no reply was ever sent.
    sent = False
    try:
        sent = await _send_chat_reply(
            channel, adapter, binding, addressee,
            str(command.conversation_id or command.actor_id or ""), str(turn["reply"] or ""),
        )
    except Exception as exc:
        _log.warning("gateway.chat_reply_failed", channel=channel, error=str(exc))

    return {
        "status": "ok", "channel": channel, "session_id": turn["session_id"],
        "actions": turn.get("actions", []), "reply": turn["reply"], "reply_sent": sent,
    }


# ── Generic webhook ───────────────────────────────────────────────────────────


@router.post(
    "/{org_id}/webhook",
    operation_id="gateway_webhook",
    summary="Generic HMAC-signed webhook receiver",
)
async def generic_webhook(
    org_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    raw_body = await request.body()
    raw = await request.json()
    headers = dict(request.headers)

    await _authenticate_channel(_webhook, "webhook", headers, raw, raw_body=raw_body)

    tenant_id = trusted_gateway_tenant(headers)  # spoof-proof: gated by ingress secret
    command = await _webhook.normalize(raw, tenant_id=tenant_id, org_id=org_id)

    background_tasks.add_task(_process_command, command, request.app.state)
    return {"command_id": command.command_id, "status": "queued"}


# ── Gateway config ────────────────────────────────────────────────────────────
#
# These three endpoints used to be stubs that faked success: GET /config returned
# hard-coded defaults, PUT /config echoed the request body back WITHOUT storing
# it, and GET /channels/status returned canned "active"/"not_configured" statuses
# (plus a made-up wss://mcp.agentverse.io endpoint) for every org. They also sit
# under the /v1/gateway/ TenantMiddleware bypass, so they answered anyone, for any
# org id. A client "saving" config got 200 and the change silently vanished.
#
# There is no gateway-config store, nothing reads these flags (channel webhooks
# are gated by their own env-configured secrets), and persisting them durably
# would need tenant auth on a bypassed prefix plus vault storage for the channel
# credentials the frontend sends — so they now answer 501 honestly instead.

_GATEWAY_CONFIG_NOT_IMPLEMENTED = (
    "Gateway channel configuration is not implemented: channels are configured via "
    "environment (TELEGRAM_*/SLACK_*/WHATSAPP_*/TEAMS_*, GATEWAY_INGRESS_SECRET, "
    "VOICE_PHONE_*) and nothing is persisted per org."
)


@router.get(
    "/{org_id}/config",
    operation_id="gateway_get_config",
    summary="Get gateway channel configuration (not implemented — 501)",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    responses={501: {"description": "Not implemented"}},
)
async def get_config(org_id: str) -> GatewayConfig:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _GATEWAY_CONFIG_NOT_IMPLEMENTED)


@router.put(
    "/{org_id}/config",
    operation_id="gateway_update_config",
    summary="Update gateway channel configuration (not implemented — 501)",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    responses={501: {"description": "Not implemented"}},
)
async def update_config(org_id: str, config: GatewayConfig) -> GatewayConfig:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _GATEWAY_CONFIG_NOT_IMPLEMENTED)


# ── Q10: Gateway Admin UI endpoints ──────────────────────────────────────────


@router.get(
    "/{org_id}/channels/status",
    operation_id="gateway_channel_status",
    summary="Q10 — Channel connection statuses (not implemented — 501)",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    responses={501: {"description": "Not implemented"}},
)
async def get_channel_status(org_id: str) -> dict[str, Any]:
    """Was a hard-coded status list identical for every org; see the note above."""
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _GATEWAY_CONFIG_NOT_IMPLEMENTED)
