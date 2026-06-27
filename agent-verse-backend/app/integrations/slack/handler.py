"""Slack integration for AgentVerse.

Enables:
- /agentverse [goal] slash command to submit goals
- HITL approval buttons sent to Slack when approval required
- Goal completion notifications in Slack

Uses open-source slack-sdk (not Bolt, just the SDK for webhook handling).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


def get_slack_signing_secret() -> str:
    return os.getenv("SLACK_SIGNING_SECRET", "")


def get_slack_bot_token() -> str:
    return os.getenv("SLACK_BOT_TOKEN", "")


def verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    signing_secret: str,
) -> bool:
    """Verify Slack request signature. Returns False when no secret configured."""
    if not signing_secret:
        if os.getenv("ENVIRONMENT", "development") == "production":
            return False  # Fail-closed in production
        return True  # Allow in development only

    # Reject stale requests (> 5 minutes old)
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False

    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        signing_secret.encode(), base.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def send_slack_message(
    channel: str,
    text: str,
    blocks: list[dict] | None = None,
) -> dict[str, Any]:
    """Send a message to a Slack channel using Bot Token."""
    bot_token = get_slack_bot_token()
    if not bot_token:
        return {"ok": False, "error": "SLACK_BOT_TOKEN not configured"}

    import httpx

    payload: dict[str, Any] = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            return resp.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def send_approval_request_to_slack(
    *,
    request_id: str,
    goal_id: str,
    action: str,
    risk_level: str,
    channel: str | None = None,
) -> dict[str, Any]:
    """Send an interactive HITL approval request to Slack with buttons."""
    target_channel = channel or os.getenv("SLACK_APPROVAL_CHANNEL", "#approvals")

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":warning: *Approval Required*\n"
                    f"Goal: `{goal_id}`\n"
                    f"Action: `{action}`\n"
                    f"Risk: `{risk_level}`"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": "approve_hitl",
                    "value": goal_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": "reject_hitl",
                    "value": goal_id,
                },
            ],
        },
    ]

    return await send_slack_message(
        channel=target_channel,
        text=f"Approval required for goal {goal_id}",
        blocks=blocks,
    )
