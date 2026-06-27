"""Brevo (formerly Sendinblue) MCP server — email, contacts, campaigns.

Environment:
  BREVO_API_KEY: Brevo API key (api-key v3)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BREVO_BASE = "https://api.brevo.com/v3"

TOOL_DEFINITIONS = [
    {
        "name": "brevo_send_email",
        "description": "Send a transactional email via Brevo",
        "parameters": {
            "type": "object",
            "properties": {
                "to_email": {"type": "string"},
                "to_name": {"type": "string", "default": ""},
                "subject": {"type": "string"},
                "html_content": {"type": "string", "description": "HTML body"},
                "text_content": {"type": "string", "description": "Plain text body"},
                "sender_email": {"type": "string"},
                "sender_name": {"type": "string", "default": ""},
                "reply_to": {"type": "string"},
                "template_id": {"type": "integer", "description": "Brevo template ID"},
                "params": {"type": "object", "description": "Template parameter values"},
            },
            "required": ["to_email", "subject"],
        },
    },
    {
        "name": "brevo_list_contacts",
        "description": "List Brevo contacts",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
                "email_blacklisted": {"type": "boolean"},
            },
        },
    },
    {
        "name": "brevo_create_contact",
        "description": "Create a new Brevo contact",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "list_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Brevo list IDs to add contact to",
                },
                "attributes": {"type": "object", "description": "Custom contact attributes"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "brevo_list_campaigns",
        "description": "List Brevo email campaigns",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["classic", "trigger"],
                    "default": "classic",
                },
                "status": {
                    "type": "string",
                    "enum": ["draft", "sent", "archive", "queued", "suspended", "inProcess"],
                },
                "limit": {"type": "integer", "default": 10},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "brevo_get_contact",
        "description": "Get a Brevo contact by email",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "brevo_delete_contact",
        "description": "Delete a Brevo contact by email",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
            },
            "required": ["email"],
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("BREVO_API_KEY", "")
    return {
        "api-key": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("BREVO_API_KEY"):
        return {"error": "BREVO_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=BREVO_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "brevo_send_email":
                sender_email = arguments.get("sender_email") or os.getenv(
                    "BREVO_SENDER_EMAIL", "noreply@example.com"
                )
                payload: dict[str, Any] = {
                    "to": [{"email": arguments["to_email"], "name": arguments.get("to_name", "")}],
                    "subject": arguments["subject"],
                    "sender": {"email": sender_email, "name": arguments.get("sender_name", "")},
                }
                if "template_id" in arguments:
                    payload["templateId"] = arguments["template_id"]
                    if "params" in arguments:
                        payload["params"] = arguments["params"]
                else:
                    if arguments.get("html_content"):
                        payload["htmlContent"] = arguments["html_content"]
                    if arguments.get("text_content"):
                        payload["textContent"] = arguments["text_content"]
                if "reply_to" in arguments:
                    payload["replyTo"] = {"email": arguments["reply_to"]}
                r = await c.post("/smtp/email", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"message_id": data.get("messageId")}

            elif tool_name == "brevo_list_contacts":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 50),
                    "offset": arguments.get("offset", 0),
                }
                if "email_blacklisted" in arguments:
                    params["emailBlacklisted"] = arguments["email_blacklisted"]
                r = await c.get("/contacts", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "contacts": [
                        {
                            "id": ct.get("id"),
                            "email": ct.get("email"),
                            "first_name": ct.get("attributes", {}).get("FIRSTNAME", ""),
                            "last_name": ct.get("attributes", {}).get("LASTNAME", ""),
                        }
                        for ct in data.get("contacts", [])
                    ],
                    "count": data.get("count", 0),
                }

            elif tool_name == "brevo_create_contact":
                payload = {"email": arguments["email"]}
                attrs: dict[str, Any] = {}
                if "first_name" in arguments:
                    attrs["FIRSTNAME"] = arguments["first_name"]
                if "last_name" in arguments:
                    attrs["LASTNAME"] = arguments["last_name"]
                if "attributes" in arguments:
                    attrs.update(arguments["attributes"])
                if attrs:
                    payload["attributes"] = attrs
                if "list_ids" in arguments:
                    payload["listIds"] = arguments["list_ids"]
                r = await c.post("/contacts", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id")}

            elif tool_name == "brevo_list_campaigns":
                params = {
                    "type": arguments.get("type", "classic"),
                    "limit": arguments.get("limit", 10),
                    "offset": arguments.get("offset", 0),
                }
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await c.get("/emailCampaigns", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "campaigns": [
                        {
                            "id": camp.get("id"),
                            "name": camp.get("name"),
                            "status": camp.get("status"),
                            "subject": camp.get("subject", ""),
                        }
                        for camp in data.get("campaigns", [])
                    ],
                    "count": data.get("count", 0),
                }

            elif tool_name == "brevo_get_contact":
                import urllib.parse
                email_enc = urllib.parse.quote(arguments["email"])
                r = await c.get(f"/contacts/{email_enc}")
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "email": data.get("email"),
                    "attributes": data.get("attributes", {}),
                }

            elif tool_name == "brevo_delete_contact":
                import urllib.parse
                email_enc = urllib.parse.quote(arguments["email"])
                r = await c.delete(f"/contacts/{email_enc}")
                return {"success": r.status_code == 204, "status_code": r.status_code}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("brevo_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
