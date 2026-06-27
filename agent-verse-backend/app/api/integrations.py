"""Integration endpoints for Slack, Zapier, and email triggers."""
from __future__ import annotations

import json
import os
import urllib.parse
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _get_slack_tenant_id() -> str:
    return os.getenv("SLACK_TENANT_ID", "")


def _get_zapier_tenant_id() -> str:
    return os.getenv("ZAPIER_TENANT_ID", "")


# ── Slack ──────────────────────────────────────────────────────────────────────

@router.post("/slack/commands")
async def slack_slash_command(
    request: Request,
    x_slack_signature: str = Header(default=""),
    x_slack_request_timestamp: str = Header(default=""),
) -> Any:
    """Handle /agentverse Slack slash command."""
    body = await request.body()

    from app.integrations.slack.handler import (
        get_slack_signing_secret,
        verify_slack_signature,
    )

    if not verify_slack_signature(
        body,
        x_slack_request_timestamp,
        x_slack_signature,
        get_slack_signing_secret(),
    ):
        raise HTTPException(403, "Invalid Slack signature")

    params = dict(urllib.parse.parse_qsl(body.decode()))

    text = params.get("text", "").strip()
    user_id = params.get("user_id", "unknown")

    if not text:
        return {
            "response_type": "ephemeral",
            "text": "Usage: /agentverse [your goal description]",
        }

    goal_service = getattr(request.app.state, "goal_service", None)
    if goal_service is None:
        return {"response_type": "ephemeral", "text": "AgentVerse service unavailable"}

    from app.tenancy.context import PlanTier, TenantContext

    slack_tenant_id = _get_slack_tenant_id()
    if not slack_tenant_id:
        # Try legacy settings attribute before failing
        slack_tenant_id = (
            request.app.state.settings.slack_tenant_id
            if hasattr(request.app.state.settings, "slack_tenant_id")
            else ""
        )
    if not slack_tenant_id:
        return {
            "response_type": "ephemeral",
            "text": "⚠️ Slack integration not configured. Ask admin to set SLACK_TENANT_ID env var.",
        }

    ctx = TenantContext(
        tenant_id=slack_tenant_id,
        plan=PlanTier.PROFESSIONAL,
        api_key_id=f"slack:{user_id}",
    )

    try:
        result = await goal_service.submit_goal(
            goal=text,
            priority="normal",
            dry_run=False,
            tenant_ctx=ctx,
        )
        return {
            "response_type": "in_channel",
            "text": (
                f"Goal submitted! *{text[:100]}*\n"
                f"Goal ID: `{result['goal_id']}`"
            ),
        }
    except Exception as exc:
        return {"response_type": "ephemeral", "text": f"Error: {exc}"}


@router.post("/slack/events")
async def slack_events(
    request: Request,
    x_slack_signature: str = Header(default=""),
    x_slack_request_timestamp: str = Header(default=""),
) -> Any:
    """Handle Slack event callbacks (interactive buttons, etc.)."""
    body = await request.body()

    from app.integrations.slack.handler import (
        get_slack_signing_secret,
        verify_slack_signature,
    )

    if get_slack_signing_secret() and not verify_slack_signature(
        body,
        x_slack_request_timestamp,
        x_slack_signature,
        get_slack_signing_secret(),
    ):
        raise HTTPException(403, "Invalid Slack signature")

    try:
        data = json.loads(body)
    except Exception:
        params = dict(urllib.parse.parse_qsl(body.decode()))
        data = json.loads(params.get("payload", "{}"))

    # Handle URL verification challenge
    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}

    # Handle interactive button presses (HITL approve/reject)
    if data.get("type") == "block_actions":
        for action in data.get("actions", []):
            action_id = action.get("action_id")
            request_id = action.get("value", "")
            if not request_id:
                continue

            hitl = getattr(request.app.state, "hitl_gateway", None)
            if hitl:
                from app.tenancy.context import PlanTier, TenantContext

                ctx = TenantContext(
                    tenant_id=_get_slack_tenant_id() or "slack-events",
                    plan=PlanTier.PROFESSIONAL,
                    api_key_id="slack-button",
                )
                approver = data.get("user", {}).get("name", "slack-user")

                if action_id == "approve_hitl":
                    hitl.approve(request_id, approver=approver, tenant_ctx=ctx)
                elif action_id == "reject_hitl":
                    hitl.reject(request_id, approver=approver, tenant_ctx=ctx)

    return {"ok": True}


# ── Zapier ─────────────────────────────────────────────────────────────────────


class ZapierTriggerRequest(BaseModel):
    goal: str | None = None
    text: str | None = None
    message: str | None = None
    agent_id: str | None = None
    priority: str = "normal"
    metadata: dict[str, Any] = {}


@router.post("/zapier/trigger")
async def zapier_trigger(
    request: Request,
    body: ZapierTriggerRequest,
    x_zapier_secret: str = Header(default=""),
) -> dict[str, Any]:
    """Zapier sends data → create AgentVerse goal."""
    from app.integrations.zapier.handler import verify_zapier_secret

    if not verify_zapier_secret(x_zapier_secret):
        raise HTTPException(403, "Invalid Zapier secret")

    goal_text = body.goal or body.text or body.message
    if not goal_text:
        raise HTTPException(422, "No goal text found. Set 'goal', 'text', or 'message'")

    goal_service = getattr(request.app.state, "goal_service", None)
    if not goal_service:
        raise HTTPException(503, "Goal service unavailable")

    from app.tenancy.context import PlanTier, TenantContext

    zapier_tenant_id = _get_zapier_tenant_id()
    if not zapier_tenant_id:
        raise HTTPException(
            503, "Zapier integration not configured. Set ZAPIER_TENANT_ID env var."
        )

    ctx = TenantContext(
        tenant_id=zapier_tenant_id,
        plan=PlanTier.PROFESSIONAL,
        api_key_id="zapier",
    )

    result = await goal_service.submit_goal(
        goal=goal_text,
        priority=body.priority,
        dry_run=False,
        tenant_ctx=ctx,
        agent_id=body.agent_id,
    )

    return {
        "goal_id": result["goal_id"],
        "status": result.get("status", "planning"),
        "goal": goal_text[:200],
        "message": "Goal submitted successfully",
        "track_url": f"/goals/{result['goal_id']}",
    }


@router.get("/zapier/goals")
async def zapier_poll_completed_goals(request: Request) -> list[dict[str, Any]]:
    """Zapier polling trigger — returns recently completed goals."""
    goal_service = getattr(request.app.state, "goal_service", None)
    if not goal_service:
        return []

    from app.tenancy.context import PlanTier, TenantContext

    zapier_tenant_id = _get_zapier_tenant_id()
    if not zapier_tenant_id:
        return []

    ctx = TenantContext(
        tenant_id=zapier_tenant_id,
        plan=PlanTier.PROFESSIONAL,
        api_key_id="zapier-poll",
    )

    try:
        result = await goal_service.list_goals(tenant_ctx=ctx)
        completed = [
            g for g in result.get("goals", []) if g.get("status") == "complete"
        ]
        return completed[:10]
    except Exception:
        return []
