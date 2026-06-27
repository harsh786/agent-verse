"""Customer.io MCP server — customers, events, and campaigns.

Environment:
  CUSTOMERIO_SITE_ID: Customer.io Site ID (for tracking API)
  CUSTOMERIO_API_KEY: Customer.io API key (for tracking API)
  CUSTOMERIO_APP_API_KEY: Customer.io App API key (for app API v1)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TRACK_BASE = "https://track.customer.io/api/v1"
APP_BASE = "https://api.customer.io/v1"

TOOL_DEFINITIONS = [
    {
        "name": "customerio_identify",
        "description": "Create or update a customer profile in Customer.io",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "email": {"type": "string"},
                "attributes": {"type": "object", "description": "Customer attributes"},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "customerio_track_event",
        "description": "Track a named event for a customer",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "event_name": {"type": "string"},
                "data": {"type": "object", "description": "Event data/properties"},
            },
            "required": ["customer_id", "event_name"],
        },
    },
    {
        "name": "customerio_delete_customer",
        "description": "Delete a customer from Customer.io",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "customerio_send_email",
        "description": "Send a transactional email via Customer.io",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email"},
                "transactional_message_id": {"type": "string", "description": "Customer.io transactional message ID"},
                "message_data": {"type": "object", "description": "Template variable values"},
                "from_email": {"type": "string"},
                "reply_to": {"type": "string"},
                "bcc": {"type": "string"},
            },
            "required": ["to", "transactional_message_id"],
        },
    },
    {
        "name": "customerio_list_customers",
        "description": "Search for customers in Customer.io (App API)",
        "parameters": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "description": "Customer.io segment filter object",
                },
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "customerio_suppress_customer",
        "description": "Suppress a customer to stop all messages",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
            },
            "required": ["customer_id"],
        },
    },
]


def _track_headers() -> dict[str, str]:
    import base64
    site_id = os.getenv("CUSTOMERIO_SITE_ID", "")
    api_key = os.getenv("CUSTOMERIO_API_KEY", "")
    creds = base64.b64encode(f"{site_id}:{api_key}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
    }


def _app_headers() -> dict[str, str]:
    app_key = os.getenv("CUSTOMERIO_APP_API_KEY", "")
    return {
        "Authorization": f"Bearer {app_key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("CUSTOMERIO_SITE_ID") and not os.getenv("CUSTOMERIO_APP_API_KEY"):
        return {"error": "CUSTOMERIO_SITE_ID/CUSTOMERIO_API_KEY or CUSTOMERIO_APP_API_KEY required"}

    try:
        if tool_name in ("customerio_identify", "customerio_track_event", "customerio_delete_customer"):
            async with httpx.AsyncClient(
                base_url=TRACK_BASE, headers=_track_headers(), timeout=30.0
            ) as c:
                if tool_name == "customerio_identify":
                    payload: dict[str, Any] = {}
                    if "email" in arguments:
                        payload["email"] = arguments["email"]
                    if "attributes" in arguments:
                        payload.update(arguments["attributes"])
                    r = await c.put(f"/customers/{arguments['customer_id']}", json=payload)
                    return {"success": r.status_code == 200, "status_code": r.status_code}

                elif tool_name == "customerio_track_event":
                    payload = {
                        "name": arguments["event_name"],
                        "data": arguments.get("data", {}),
                    }
                    r = await c.post(
                        f"/customers/{arguments['customer_id']}/events", json=payload
                    )
                    return {"success": r.status_code == 200, "status_code": r.status_code}

                elif tool_name == "customerio_delete_customer":
                    r = await c.delete(f"/customers/{arguments['customer_id']}")
                    return {"success": r.status_code == 200, "status_code": r.status_code}

        else:
            async with httpx.AsyncClient(
                base_url=APP_BASE, headers=_app_headers(), timeout=30.0
            ) as c:
                if tool_name == "customerio_send_email":
                    payload = {
                        "to": arguments["to"],
                        "transactional_message_id": arguments["transactional_message_id"],
                        "message_data": arguments.get("message_data", {}),
                    }
                    for opt in ("from_email", "reply_to", "bcc"):
                        if opt in arguments:
                            payload[opt] = arguments[opt]
                    r = await c.post("/send/email", json=payload)
                    r.raise_for_status()
                    data = r.json()
                    return {"delivery_id": data.get("delivery_id")}

                elif tool_name == "customerio_list_customers":
                    payload = {
                        "filter": arguments.get("filter", {}),
                        "limit": arguments.get("limit", 50),
                    }
                    r = await c.post("/customers", json=payload)
                    r.raise_for_status()
                    data = r.json()
                    return {
                        "customers": data.get("results", []),
                        "total": data.get("total", 0),
                    }

                elif tool_name == "customerio_suppress_customer":
                    r = await c.post(f"/customers/{arguments['customer_id']}/suppress")
                    return {"success": r.status_code in (200, 204), "status_code": r.status_code}

        return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("customerio_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
