"""Gateway router — FastAPI endpoints for all channels.

New endpoints:
  POST /v1/gateway/{org_id}/telegram/webhook
  POST /v1/gateway/{org_id}/slack/events
  GET  /v1/gateway/{org_id}/slack/events  (URL verification)
  POST /v1/gateway/{org_id}/whatsapp/webhook
  GET  /v1/gateway/{org_id}/whatsapp/webhook  (verification)
  POST /v1/gateway/{org_id}/teams/messages
  POST /v1/gateway/{org_id}/webhook
  GET  /v1/gateway/{org_id}/config
  PUT  /v1/gateway/{org_id}/config
  GET  /v1/gateway/{org_id}/conversations
  GET  /v1/gateway/{org_id}/history
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from opentelemetry import trace
from pydantic import BaseModel

from app.gateway.channels.slack import SlackChannelAdapter
from app.gateway.channels.teams import MicrosoftTeamsAdapter
from app.gateway.channels.telegram import TelegramChannelAdapter
from app.gateway.channels.webhook import WebhookChannelAdapter
from app.gateway.channels.whatsapp import WhatsAppChannelAdapter
from app.gateway.command import OrgCommand, OrgResponse

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

router = APIRouter(prefix="/v1/gateway", tags=["gateway"])

# ── Channel adapter singletons ────────────────────────────────────────────────
_telegram = TelegramChannelAdapter()
_slack = SlackChannelAdapter()
_whatsapp = WhatsAppChannelAdapter()
_teams = MicrosoftTeamsAdapter()
_webhook = WebhookChannelAdapter()


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


def _tenant_ctx_for(command: OrgCommand) -> Any:
    """Build a TenantContext for a gateway command. The webhook caller supplies
    the tenant via the ``x-tenant-id`` header (adapters put it on the command);
    fall back to org_id when a deployment maps 1:1 org→tenant."""
    from app.tenancy.context import PlanTier, TenantContext

    tenant_id = command.tenant_id or command.org_id
    return TenantContext(tenant_id=tenant_id, api_key_id="gateway", plan=PlanTier.FREE), tenant_id


async def _download_command_file(cf: Any) -> bytes | None:
    if getattr(cf, "data", None):
        return cf.data
    url = getattr(cf, "url", None)
    if not url:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url)
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
            source_url=getattr(cf, "url", "") or "",
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


async def _process_command(command: OrgCommand) -> OrgResponse:
    """Turn an inbound chat command into real platform work: ingest any attached
    documents into knowledge, submit the text as a goal, and reply to the chat."""
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

        try:
            from app.main import app as _fastapi_app

            state = _fastapi_app.state
        except Exception:
            state = None

        ctx, tenant_id = _tenant_ctx_for(command)
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

    if not await _telegram.verify_auth(headers, raw):
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook token")

    tenant_id = headers.get("x-tenant-id", "")
    command = await _telegram.normalize(raw, tenant_id=tenant_id, org_id=org_id)

    if command.text:
        background_tasks.add_task(_process_command, command)

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
    raw = await request.json()
    headers = dict(request.headers)

    # URL verification challenge
    if raw.get("type") == "url_verification":
        return {"challenge": raw.get("challenge", "")}

    if not await _slack.verify_auth(headers, raw):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    tenant_id = headers.get("x-tenant-id", "")
    command = await _slack.normalize(raw, tenant_id=tenant_id, org_id=org_id)

    if command.text:
        background_tasks.add_task(_process_command, command)

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
    import os

    if hub_mode == "subscribe" and hub_verify_token == os.getenv("WHATSAPP_VERIFY_TOKEN", ""):
        return int(hub_challenge)
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
    raw = await request.json()
    headers = dict(request.headers)

    if not await _whatsapp.verify_auth(headers, raw):
        raise HTTPException(status_code=403, detail="Invalid WhatsApp signature")

    tenant_id = headers.get("x-tenant-id", "")
    command = await _whatsapp.normalize(raw, tenant_id=tenant_id, org_id=org_id)

    if command.text:
        background_tasks.add_task(_process_command, command)

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

    tenant_id = headers.get("x-tenant-id", "")
    command = await _teams.normalize(raw, tenant_id=tenant_id, org_id=org_id)

    if command.text:
        background_tasks.add_task(_process_command, command)

    return {"type": "message", "text": ""}


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
    raw = await request.json()
    headers = dict(request.headers)

    if not await _webhook.verify_auth(headers, raw):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    tenant_id = headers.get("x-tenant-id", "")
    command = await _webhook.normalize(raw, tenant_id=tenant_id, org_id=org_id)

    background_tasks.add_task(_process_command, command)
    return {"command_id": command.command_id, "status": "queued"}


# ── Gateway config ────────────────────────────────────────────────────────────


@router.get(
    "/{org_id}/config",
    operation_id="gateway_get_config",
    summary="Get gateway channel configuration",
)
async def get_config(org_id: str) -> GatewayConfig:
    return GatewayConfig()


@router.put(
    "/{org_id}/config",
    operation_id="gateway_update_config",
    summary="Update gateway channel configuration",
)
async def update_config(org_id: str, config: GatewayConfig) -> GatewayConfig:
    return config


# ── Q10: Gateway Admin UI endpoints ──────────────────────────────────────────


@router.get(
    "/{org_id}/channels/status",
    operation_id="gateway_channel_status",
    summary="Q10 — Get all channel connection statuses",
)
async def get_channel_status(org_id: str) -> dict[str, Any]:
    """
    Q10: Gateway Admin UI — shows all active channels and their status.
    SETTINGS → Command Gateway → ACTIVE CHANNELS
    """
    return {
        "org_id": org_id,
        "channels": [
            {
                "name": "rest",
                "label": "REST API",
                "enabled": True,
                "status": "active",
                "endpoint": f"/v1/org/{org_id}/command",
            },
            {
                "name": "telegram",
                "label": "Telegram",
                "enabled": False,
                "status": "not_configured",
                "setup_url": f"/gateway/{org_id}/setup/telegram",
            },
            {
                "name": "slack",
                "label": "Slack",
                "enabled": False,
                "status": "not_configured",
                "setup_url": f"/gateway/{org_id}/setup/slack",
            },
            {
                "name": "teams",
                "label": "Teams",
                "enabled": False,
                "status": "not_configured",
                "setup_url": f"/gateway/{org_id}/setup/teams",
            },
            {"name": "discord", "label": "Discord", "enabled": False, "status": "not_configured"},
            {"name": "whatsapp", "label": "WhatsApp", "enabled": False, "status": "not_configured"},
            {"name": "email", "label": "Email", "enabled": False, "status": "not_configured"},
            {
                "name": "mcp",
                "label": "MCP Server",
                "enabled": True,
                "status": "active",
                "endpoint": f"wss://mcp.agentverse.io/v1/org/{org_id}",
            },
            {"name": "webhook", "label": "Webhooks", "enabled": True, "status": "active"},
        ],
        "gateway_config": {
            "max_commands_per_hour": 100,
            "require_2fa_for": ["approve", "change-autonomy", "delete"],
            "response_language": "auto",
            "log_all_commands": True,
        },
    }
