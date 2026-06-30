"""Snov.io MCP server — lead generation: email finding, verification, and prospect management.

Environment variables:
  SNOVIO_CLIENT_ID: Snov.io OAuth application client ID
  SNOVIO_CLIENT_SECRET: Snov.io OAuth application client secret
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

SNOVIO_BASE = "https://api.snov.io"

TOOL_DEFINITIONS = [
    {
        "name": "snovio_find_emails",
        "description": "Find email addresses for a person at a company using name and domain",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Company website domain, e.g. 'google.com'"},
                "first_name": {"type": "string", "description": "Person's first name"},
                "last_name": {"type": "string", "description": "Person's last name"},
            },
            "required": ["domain", "first_name", "last_name"],
        },
    },
    {
        "name": "snovio_verify_email",
        "description": "Verify whether an email address is valid and deliverable",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Email address to verify"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "snovio_add_prospect",
        "description": "Add a prospect to a Snov.io list for outreach",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Prospect's email address"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "company_name": {"type": "string"},
                "list_id": {"type": "string", "description": "Snov.io list ID to add prospect to"},
            },
            "required": ["email", "list_id"],
        },
    },
    {
        "name": "snovio_list_prospects",
        "description": "List prospects in a Snov.io list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "Snov.io list ID"},
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
            },
            "required": ["list_id"],
        },
    },
    {
        "name": "snovio_send_email",
        "description": "Send an email to a prospect via a Snov.io campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Snov.io campaign ID"},
                "email": {"type": "string", "description": "Recipient email address"},
                "from_name": {"type": "string", "description": "Sender name"},
                "from_email": {"type": "string", "description": "Sender email address"},
            },
            "required": ["campaign_id", "email"],
        },
    },
    {
        "name": "snovio_get_stats",
        "description": "Get email delivery and engagement statistics for a Snov.io campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Campaign ID to get stats for"},
            },
            "required": ["campaign_id"],
        },
    },
]


async def _get_access_token(client_id: str, client_secret: str) -> str:
    """Obtain OAuth 2.0 access token from Snov.io."""
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(
            f"{SNOVIO_BASE}/v1/oauth/access_token",
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    client_id = os.getenv("SNOVIO_CLIENT_ID", "")
    client_secret = os.getenv("SNOVIO_CLIENT_SECRET", "")
    if not client_id:
        return {"error": "SNOVIO_CLIENT_ID not configured"}
    if not client_secret:
        return {"error": "SNOVIO_CLIENT_SECRET not configured"}

    try:
        access_token = await _get_access_token(client_id, client_secret)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(
            base_url=SNOVIO_BASE, headers=headers, timeout=30.0
        ) as c:
            if tool_name == "snovio_find_emails":
                body: dict[str, Any] = {
                    "domain": arguments["domain"],
                    "firstName": arguments["first_name"],
                    "lastName": arguments["last_name"],
                }
                r = await c.post("/v2/get-emails-from-names", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "snovio_verify_email":
                r = await c.post(
                    "/v1/get-emails-verification-status",
                    json={"emails": [arguments["email"]]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "snovio_add_prospect":
                body = {
                    "email": arguments["email"],
                    "listId": arguments["list_id"],
                }
                for k in ("first_name", "last_name", "company_name"):
                    if k in arguments:
                        body[k.replace("_", "")] = arguments[k]
                r = await c.post("/v1/add-prospect-to-list", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "snovio_list_prospects":
                params: dict[str, Any] = {
                    "listId": arguments["list_id"],
                    "page": arguments.get("page", 1),
                    "perPage": arguments.get("per_page", 25),
                }
                r = await c.get("/v2/prospect-list", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "snovio_send_email":
                body = {
                    "campaignId": arguments["campaign_id"],
                    "email": arguments["email"],
                }
                for k in ("from_name", "from_email"):
                    if k in arguments:
                        body[k.replace("_", "")] = arguments[k]
                r = await c.post("/v1/campaigns/send", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "snovio_get_stats":
                r = await c.get(
                    f"/v2/campaign-stats",
                    params={"campaignId": arguments["campaign_id"]},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("snovio_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
