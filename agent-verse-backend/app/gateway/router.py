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


async def _process_command(command: OrgCommand) -> OrgResponse:
    """Route command to org brain and return response."""
    with _tracer.start_as_current_span("gateway.process_command") as span:
        span.set_attribute("channel", command.actor_channel)
        span.set_attribute("org_id", command.org_id)
        span.set_attribute("command_id", command.command_id)
        _log.info(
            "gateway.command_received",
            channel=command.actor_channel,
            org_id=command.org_id,
            text_preview=command.text[:60],
        )
        # Delegate to org command endpoint via internal call
        # In production this calls the OrgService.process_command()
        # For now: stub response acknowledging receipt
        return OrgResponse(
            command_id=command.command_id,
            text=f"Processing: {command.text[:100]}",
            status="processing",
            mission_id=None,
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
