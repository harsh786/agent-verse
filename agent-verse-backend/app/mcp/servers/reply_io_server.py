"""Reply.io MCP server — sales automation: people, sequences, and email statistics.

Environment variables:
  REPLYIO_API_KEY: Reply.io API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

REPLYIO_BASE = "https://api.reply.io/v1"

TOOL_DEFINITIONS = [
    {
        "name": "replyio_add_person",
        "description": "Add a new person (prospect) to a Reply.io campaign list",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Person's email address"},
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "company": {"type": "string"},
                "title": {"type": "string"},
                "phone": {"type": "string"},
                "linkedin": {"type": "string", "description": "LinkedIn profile URL"},
                "customFields": {
                    "type": "object",
                    "description": "Custom variable key-value pairs for personalisation",
                },
            },
            "required": ["email"],
        },
    },
    {
        "name": "replyio_list_people",
        "description": "List people in Reply.io with optional filtering by email or name",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "limit": {"type": "integer", "default": 25},
                "email": {"type": "string", "description": "Filter by email"},
                "campaignId": {"type": "integer", "description": "Filter by campaign ID"},
            },
        },
    },
    {
        "name": "replyio_push_to_sequence",
        "description": "Push a person into a Reply.io email sequence",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Person's email address"},
                "sequenceId": {"type": "integer", "description": "Reply.io sequence ID"},
            },
            "required": ["email", "sequenceId"],
        },
    },
    {
        "name": "replyio_list_sequences",
        "description": "List all email sequences in Reply.io",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "limit": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "replyio_get_email_stats",
        "description": "Get email delivery and engagement statistics for a Reply.io campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaignId": {"type": "integer", "description": "Campaign ID to get stats for"},
                "dateFrom": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "dateTo": {"type": "string", "description": "End date (YYYY-MM-DD)"},
            },
            "required": ["campaignId"],
        },
    },
    {
        "name": "replyio_pause_sequence",
        "description": "Pause a person's active sequence in Reply.io",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Person's email address"},
                "sequenceId": {"type": "integer", "description": "Sequence ID to pause"},
            },
            "required": ["email", "sequenceId"],
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("REPLYIO_API_KEY", "")
    if not api_key:
        return {"error": "REPLYIO_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=REPLYIO_BASE, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "replyio_add_person":
                body: dict[str, Any] = {"email": arguments["email"]}
                for k in ("firstName", "lastName", "company", "title", "phone", "linkedin"):
                    if k in arguments:
                        body[k] = arguments[k]
                if "customFields" in arguments:
                    body["customFields"] = arguments["customFields"]
                r = await c.post("/people", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "replyio_list_people":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "limit": arguments.get("limit", 25),
                }
                if "email" in arguments:
                    params["email"] = arguments["email"]
                if "campaignId" in arguments:
                    params["campaignId"] = arguments["campaignId"]
                r = await c.get("/people", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "replyio_push_to_sequence":
                body = {
                    "email": arguments["email"],
                    "sequenceId": arguments["sequenceId"],
                }
                r = await c.post("/actions/addtocampaign", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "replyio_list_sequences":
                params = {
                    "page": arguments.get("page", 1),
                    "limit": arguments.get("limit", 25),
                }
                r = await c.get("/campaigns", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "replyio_get_email_stats":
                params = {"campaignId": arguments["campaignId"]}
                for k in ("dateFrom", "dateTo"):
                    if k in arguments:
                        params[k] = arguments[k]
                r = await c.get("/emailsstatistic", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "replyio_pause_sequence":
                body = {
                    "email": arguments["email"],
                    "campaignId": arguments["sequenceId"],
                }
                r = await c.post("/actions/pausefrompersonal", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("replyio_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
