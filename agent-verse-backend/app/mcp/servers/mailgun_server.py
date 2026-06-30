"""Mailgun MCP server — transactional email sending, domain management, and event logs.

Environment:
  MAILGUN_API_KEY: Mailgun private API key (starts with key-)
  MAILGUN_DOMAIN: Default sending domain, e.g. mg.yourdomain.com
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.mailgun.net/v3"


def _auth() -> tuple[str, str]:
    return ("api", os.getenv("MAILGUN_API_KEY", ""))


TOOL_DEFINITIONS = [
    {
        "name": "mailgun_send_email",
        "description": "Send a transactional email via Mailgun",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address(es), comma-separated"},
                "subject": {"type": "string", "description": "Email subject line"},
                "html": {"type": "string", "description": "HTML body content"},
                "text": {"type": "string", "description": "Plain text body content"},
                "from_email": {"type": "string", "description": "Sender address (defaults to postmaster@MAILGUN_DOMAIN)"},
                "domain": {"type": "string", "description": "Mailgun domain to send from (overrides MAILGUN_DOMAIN env var)"},
            },
            "required": ["to", "subject"],
        },
    },
    {
        "name": "mailgun_list_domains",
        "description": "List all verified sending domains in the Mailgun account",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max domains to return", "default": 100},
            },
        },
    },
    {
        "name": "mailgun_get_domain_stats",
        "description": "Retrieve delivery and engagement statistics for a Mailgun domain",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain name to fetch stats for"},
                "event": {"type": "string", "description": "Comma-separated events: accepted,delivered,failed,opened,clicked,unsubscribed,complained"},
                "duration": {"type": "string", "description": "Time period, e.g. 1m, 7d, 30d", "default": "30d"},
            },
            "required": ["domain"],
        },
    },
    {
        "name": "mailgun_list_events",
        "description": "Query the Mailgun event log for a domain",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain to query events for"},
                "event": {"type": "string", "description": "Event type filter: accepted, rejected, delivered, failed, opened, clicked, unsubscribed, complained"},
                "limit": {"type": "integer", "description": "Max events to return", "default": 100},
            },
            "required": ["domain"],
        },
    },
    {
        "name": "mailgun_create_mailing_list",
        "description": "Create a new mailing list address in Mailgun",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "List email address, e.g. team@mg.yourdomain.com"},
                "name": {"type": "string", "description": "Human-readable list name"},
                "description": {"type": "string", "description": "Description of the mailing list"},
                "access_level": {"type": "string", "description": "Access level: readonly, members, everyone", "default": "readonly"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "mailgun_add_list_member",
        "description": "Add a member to a Mailgun mailing list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_address": {"type": "string", "description": "Mailing list email address"},
                "email": {"type": "string", "description": "Member email address to add"},
                "name": {"type": "string", "description": "Member display name"},
                "subscribed": {"type": "boolean", "description": "Whether the member is subscribed", "default": True},
            },
            "required": ["list_address", "email"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("MAILGUN_API_KEY"):
        return {"error": "MAILGUN_API_KEY not configured"}

    domain = os.getenv("MAILGUN_DOMAIN", "")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "mailgun_send_email":
                send_domain = arguments.get("domain") or domain
                if not send_domain:
                    return {"error": "MAILGUN_DOMAIN not configured and no domain argument provided"}
                from_addr = arguments.get("from_email") or f"postmaster@{send_domain}"
                data: dict[str, Any] = {
                    "from": from_addr,
                    "to": arguments["to"],
                    "subject": arguments["subject"],
                }
                if "html" in arguments:
                    data["html"] = arguments["html"]
                if "text" in arguments:
                    data["text"] = arguments["text"]
                r = await client.post(
                    f"{BASE_URL}/{send_domain}/messages",
                    auth=_auth(),
                    data=data,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "mailgun_list_domains":
                r = await client.get(
                    f"{BASE_URL}/domains",
                    auth=_auth(),
                    params={"limit": arguments.get("limit", 100)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "domains": [
                        {"name": d.get("name"), "state": d.get("state"), "type": d.get("type")}
                        for d in data.get("items", [])
                    ],
                    "total_count": data.get("total_count", 0),
                }

            elif tool_name == "mailgun_get_domain_stats":
                params: dict[str, Any] = {"duration": arguments.get("duration", "30d")}
                if "event" in arguments:
                    params["event"] = arguments["event"]
                r = await client.get(
                    f"{BASE_URL}/{arguments['domain']}/stats/total",
                    auth=_auth(),
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "mailgun_list_events":
                params = {"limit": arguments.get("limit", 100)}
                if "event" in arguments:
                    params["event"] = arguments["event"]
                r = await client.get(
                    f"{BASE_URL}/{arguments['domain']}/events",
                    auth=_auth(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "items": data.get("items", []),
                    "paging": data.get("paging", {}),
                }

            elif tool_name == "mailgun_create_mailing_list":
                payload: dict[str, Any] = {"address": arguments["address"]}
                for field in ("name", "description"):
                    if field in arguments:
                        payload[field] = arguments[field]
                payload["access_level"] = arguments.get("access_level", "readonly")
                r = await client.post(f"{BASE_URL}/lists", auth=_auth(), data=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "mailgun_add_list_member":
                payload = {
                    "address": arguments["email"],
                    "subscribed": str(arguments.get("subscribed", True)).lower(),
                }
                if "name" in arguments:
                    payload["name"] = arguments["name"]
                r = await client.post(
                    f"{BASE_URL}/lists/{arguments['list_address']}/members",
                    auth=_auth(),
                    data=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("mailgun_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
