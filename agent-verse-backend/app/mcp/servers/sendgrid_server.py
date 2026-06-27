"""SendGrid MCP server — transactional & marketing email via SendGrid v3 API.

Environment:
  SENDGRID_API_KEY: SendGrid API key (starts with SG.)
  SENDGRID_FROM_EMAIL: Default sender email address
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

SENDGRID_BASE = "https://api.sendgrid.com/v3"

TOOL_DEFINITIONS = [
    {
        "name": "sendgrid_send_email",
        "description": "Send a transactional email via SendGrid",
        "parameters": {
            "type": "object",
            "properties": {
                "to_email": {"type": "string"},
                "subject": {"type": "string"},
                "text": {"type": "string", "description": "Plain text body"},
                "html": {"type": "string", "description": "HTML body (overrides text)"},
                "from_email": {"type": "string", "description": "Sender address (uses SENDGRID_FROM_EMAIL if omitted)"},
                "from_name": {"type": "string"},
            },
            "required": ["to_email", "subject"],
        },
    },
    {
        "name": "sendgrid_send_bulk",
        "description": "Send an email to multiple recipients",
        "parameters": {
            "type": "object",
            "properties": {
                "to_emails": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of recipient email addresses",
                },
                "subject": {"type": "string"},
                "text": {"type": "string"},
                "html": {"type": "string"},
                "from_email": {"type": "string"},
            },
            "required": ["to_emails", "subject"],
        },
    },
    {
        "name": "sendgrid_list_templates",
        "description": "List dynamic transactional email templates",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "sendgrid_send_template",
        "description": "Send an email using a dynamic template",
        "parameters": {
            "type": "object",
            "properties": {
                "to_email": {"type": "string"},
                "template_id": {"type": "string"},
                "dynamic_template_data": {
                    "type": "object",
                    "description": "Key-value pairs to populate template variables",
                },
                "from_email": {"type": "string"},
            },
            "required": ["to_email", "template_id"],
        },
    },
    {
        "name": "sendgrid_get_stats",
        "description": "Get email sending statistics for a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD format"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD format"},
                "aggregated_by": {
                    "type": "string",
                    "enum": ["day", "week", "month"],
                    "default": "day",
                },
            },
            "required": ["start_date"],
        },
    },
    {
        "name": "sendgrid_list_contacts",
        "description": "Search marketing contacts in SendGrid",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "SGQL query, e.g. email LIKE '%@example.com'",
                    "default": "",
                },
            },
        },
    },
    {
        "name": "sendgrid_add_contacts",
        "description": "Add or update contacts in the marketing contacts list",
        "parameters": {
            "type": "object",
            "properties": {
                "contacts": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of contact objects with email and optional fields",
                },
                "list_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list IDs to add contacts to",
                },
            },
            "required": ["contacts"],
        },
    },
    {
        "name": "sendgrid_create_list",
        "description": "Create a marketing contact list",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the list"},
            },
            "required": ["name"],
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("SENDGRID_API_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("SENDGRID_API_KEY"):
        return {"error": "SENDGRID_API_KEY not configured"}

    default_from = os.getenv("SENDGRID_FROM_EMAIL", "noreply@example.com")

    try:
        async with httpx.AsyncClient(
            base_url=SENDGRID_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "sendgrid_send_email":
                from_email = arguments.get("from_email", default_from)
                from_obj: dict[str, Any] = {"email": from_email}
                if "from_name" in arguments:
                    from_obj["name"] = arguments["from_name"]
                body_content = arguments.get("html") or arguments.get("text", "")
                content_type = "text/html" if arguments.get("html") else "text/plain"
                payload: dict[str, Any] = {
                    "personalizations": [{"to": [{"email": arguments["to_email"]}]}],
                    "from": from_obj,
                    "subject": arguments["subject"],
                    "content": [{"type": content_type, "value": body_content}],
                }
                r = await c.post("/mail/send", json=payload)
                return {"success": r.status_code == 202, "status_code": r.status_code}

            elif tool_name == "sendgrid_send_bulk":
                to_list = [{"email": e} for e in arguments["to_emails"]]
                body_content = arguments.get("html") or arguments.get("text", "")
                content_type = "text/html" if arguments.get("html") else "text/plain"
                payload = {
                    "personalizations": [{"to": to_list}],
                    "from": {"email": arguments.get("from_email", default_from)},
                    "subject": arguments["subject"],
                    "content": [{"type": content_type, "value": body_content}],
                }
                r = await c.post("/mail/send", json=payload)
                return {"success": r.status_code == 202, "status_code": r.status_code}

            elif tool_name == "sendgrid_list_templates":
                r = await c.get(
                    "/templates",
                    params={
                        "generations": "dynamic",
                        "page_size": arguments.get("page_size", 10),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "templates": [
                        {"id": t["id"], "name": t.get("name"), "generation": t.get("generation")}
                        for t in data.get("templates", [])
                    ]
                }

            elif tool_name == "sendgrid_send_template":
                payload = {
                    "personalizations": [
                        {
                            "to": [{"email": arguments["to_email"]}],
                            "dynamic_template_data": arguments.get("dynamic_template_data", {}),
                        }
                    ],
                    "from": {"email": arguments.get("from_email", default_from)},
                    "template_id": arguments["template_id"],
                }
                r = await c.post("/mail/send", json=payload)
                return {"success": r.status_code == 202, "status_code": r.status_code}

            elif tool_name == "sendgrid_get_stats":
                params: dict[str, Any] = {
                    "start_date": arguments["start_date"],
                    "aggregated_by": arguments.get("aggregated_by", "day"),
                }
                if "end_date" in arguments:
                    params["end_date"] = arguments["end_date"]
                r = await c.get("/stats", params=params)
                r.raise_for_status()
                return {"stats": r.json()}

            elif tool_name == "sendgrid_list_contacts":
                query = arguments.get("query", "email IS NOT NULL")
                if not query:
                    query = "email IS NOT NULL"
                r = await c.post(
                    "/marketing/contacts/search",
                    json={"query": query},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "contacts": [
                        {"id": ct.get("id"), "email": ct.get("email"), "first_name": ct.get("first_name", "")}
                        for ct in data.get("result", [])
                    ],
                    "contact_count": data.get("contact_count", 0),
                }

            elif tool_name == "sendgrid_add_contacts":
                payload = {"contacts": arguments["contacts"]}
                if "list_ids" in arguments:
                    payload["list_ids"] = arguments["list_ids"]
                r = await c.put("/marketing/contacts", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"job_id": data.get("job_id")}

            elif tool_name == "sendgrid_create_list":
                r = await c.post(
                    "/marketing/lists", json={"name": arguments["name"]}
                )
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "name": data.get("name")}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("sendgrid_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
