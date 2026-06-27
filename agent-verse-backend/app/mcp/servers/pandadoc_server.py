"""PandaDoc MCP server — document creation, templates, and eSignature.

Environment:
  PANDADOC_API_KEY: PandaDoc API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

PANDADOC_BASE = "https://api.pandadoc.com/public/v1"

TOOL_DEFINITIONS = [
    {
        "name": "pandadoc_list_documents",
        "description": "List PandaDoc documents",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "integer",
                    "description": "0=draft, 1=sent, 2=viewed, 3=awaiting_approval, 4=approved, 5=rejected, 6=waiting_pay, 7=paid, 8=completed, 9=voided",
                },
                "q": {"type": "string", "description": "Search query"},
                "count": {"type": "integer", "default": 50},
                "page": {"type": "integer", "default": 1},
                "order_by": {"type": "string", "default": "date_created"},
                "date_from": {"type": "string", "description": "ISO 8601 date"},
                "date_to": {"type": "string", "description": "ISO 8601 date"},
            },
        },
    },
    {
        "name": "pandadoc_get_document",
        "description": "Get a PandaDoc document by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "pandadoc_create_document",
        "description": "Create a new PandaDoc document from a template or from scratch",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "template_uuid": {"type": "string", "description": "Template UUID to use"},
                "recipients": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "email": {"type": "string"},
                            "first_name": {"type": "string"},
                            "last_name": {"type": "string"},
                            "role": {"type": "string"},
                        },
                    },
                },
                "fields": {
                    "type": "object",
                    "description": "Field values to pre-fill, keyed by field name",
                },
                "tokens": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Tokens to replace in the document",
                },
            },
            "required": ["name", "recipients"],
        },
    },
    {
        "name": "pandadoc_send_document",
        "description": "Send a PandaDoc document for signing",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "message": {"type": "string", "description": "Email message to recipients"},
                "subject": {"type": "string", "description": "Email subject"},
                "silent": {"type": "boolean", "default": False, "description": "Skip email notification"},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "pandadoc_list_templates",
        "description": "List available PandaDoc document templates",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search query"},
                "count": {"type": "integer", "default": 50},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "pandadoc_get_document_status",
        "description": "Get the current status of a PandaDoc document",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
            },
            "required": ["document_id"],
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("PANDADOC_API_KEY", "")
    return {
        "Authorization": f"API-Key {key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("PANDADOC_API_KEY", "")
    if not key:
        return {"error": "PANDADOC_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(base_url=PANDADOC_BASE, headers=_headers(), timeout=30.0) as c:
            if tool_name == "pandadoc_list_documents":
                params: dict[str, Any] = {
                    "count": arguments.get("count", 50),
                    "page": arguments.get("page", 1),
                    "order_by": arguments.get("order_by", "date_created"),
                }
                for field in ["status", "q", "date_from", "date_to"]:
                    if v := arguments.get(field):
                        params[field] = v
                r = await c.get("/documents", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pandadoc_get_document":
                r = await c.get(f"/documents/{arguments['document_id']}/details")
                r.raise_for_status()
                return r.json()

            elif tool_name == "pandadoc_create_document":
                payload: dict[str, Any] = {
                    "name": arguments["name"],
                    "recipients": arguments["recipients"],
                }
                if tmpl := arguments.get("template_uuid"):
                    payload["template_uuid"] = tmpl
                if fields := arguments.get("fields"):
                    payload["fields"] = fields
                if tokens := arguments.get("tokens"):
                    payload["tokens"] = tokens
                r = await c.post("/documents", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pandadoc_send_document":
                did = arguments["document_id"]
                payload: dict[str, Any] = {"silent": arguments.get("silent", False)}
                if msg := arguments.get("message"):
                    payload["message"] = msg
                if subj := arguments.get("subject"):
                    payload["subject"] = subj
                r = await c.post(f"/documents/{did}/send", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pandadoc_list_templates":
                params = {
                    "count": arguments.get("count", 50),
                    "page": arguments.get("page", 1),
                }
                if q := arguments.get("q"):
                    params["q"] = q
                r = await c.get("/templates", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pandadoc_get_document_status":
                r = await c.get(f"/documents/{arguments['document_id']}")
                r.raise_for_status()
                data = r.json()
                return {
                    "document_id": data.get("id"),
                    "status": data.get("status"),
                    "name": data.get("name"),
                    "date_modified": data.get("date_modified"),
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("pandadoc_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
