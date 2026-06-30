"""Facebook Conversions MCP server — Facebook Conversions API server-side event tracking.

Environment:
  FACEBOOK_PIXEL_ID: Facebook Pixel ID for server-side event reporting
  FACEBOOK_ACCESS_TOKEN: Facebook Conversions API access token with ads_management permission
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "facebook_conversions_send_event",
        "description": "Send a generic server-side conversion event to Facebook via Conversions API",
        "parameters": {
            "type": "object",
            "properties": {
                "event_name": {"type": "string", "description": "Event name: Purchase, Lead, CompleteRegistration, AddToCart, etc."},
                "event_time": {"type": "integer", "description": "Unix timestamp of the event (defaults to now)"},
                "event_source_url": {"type": "string", "description": "URL where the event occurred"},
                "user_data": {
                    "type": "object",
                    "description": "Hashed user identifiers: em (email), ph (phone), fn, ln, external_id",
                },
                "custom_data": {
                    "type": "object",
                    "description": "Event-specific data: value, currency, content_ids, etc.",
                },
                "test_event_code": {"type": "string", "description": "Test event code from Events Manager for validation"},
            },
            "required": ["event_name"],
        },
    },
    {
        "name": "facebook_conversions_send_purchase_event",
        "description": "Send a Purchase conversion event to Facebook Conversions API",
        "parameters": {
            "type": "object",
            "properties": {
                "value": {"type": "number", "description": "Purchase value amount"},
                "currency": {"type": "string", "description": "ISO 4217 currency code e.g. USD", "default": "USD"},
                "order_id": {"type": "string", "description": "Order ID for deduplication"},
                "content_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Product IDs purchased",
                },
                "event_source_url": {"type": "string", "description": "Page URL where purchase happened"},
                "user_email": {"type": "string", "description": "Customer email (will be SHA256 hashed)"},
                "user_phone": {"type": "string", "description": "Customer phone (will be SHA256 hashed)"},
                "test_event_code": {"type": "string", "description": "Test event code from Events Manager"},
            },
            "required": ["value", "currency"],
        },
    },
    {
        "name": "facebook_conversions_send_lead_event",
        "description": "Send a Lead conversion event to Facebook Conversions API",
        "parameters": {
            "type": "object",
            "properties": {
                "event_source_url": {"type": "string", "description": "Lead capture page URL"},
                "user_email": {"type": "string", "description": "Lead email address (will be SHA256 hashed)"},
                "user_phone": {"type": "string", "description": "Lead phone number (will be SHA256 hashed)"},
                "lead_id": {"type": "string", "description": "CRM lead ID for deduplication"},
                "test_event_code": {"type": "string", "description": "Test event code"},
            },
        },
    },
    {
        "name": "facebook_conversions_send_page_view",
        "description": "Send a PageView event to Facebook Conversions API",
        "parameters": {
            "type": "object",
            "properties": {
                "event_source_url": {"type": "string", "description": "URL of the page viewed"},
                "user_email": {"type": "string", "description": "User email (SHA256 hashed before sending)"},
                "client_ip_address": {"type": "string", "description": "User client IP address"},
                "client_user_agent": {"type": "string", "description": "User agent string"},
                "test_event_code": {"type": "string", "description": "Test event code from Events Manager"},
            },
            "required": ["event_source_url"],
        },
    },
    {
        "name": "facebook_conversions_send_custom_event",
        "description": "Send a custom-named conversion event to Facebook Conversions API",
        "parameters": {
            "type": "object",
            "properties": {
                "event_name": {"type": "string", "description": "Custom event name"},
                "event_source_url": {"type": "string", "description": "URL where event occurred"},
                "value": {"type": "number", "description": "Monetary value of the event"},
                "currency": {"type": "string", "description": "ISO 4217 currency code"},
                "user_email": {"type": "string", "description": "User email (SHA256 hashed)"},
                "custom_properties": {"type": "object", "description": "Additional custom data properties"},
                "test_event_code": {"type": "string", "description": "Test event code"},
            },
            "required": ["event_name"],
        },
    },
    {
        "name": "facebook_conversions_test_event",
        "description": "Send a test event to validate Facebook Conversions API integration",
        "parameters": {
            "type": "object",
            "properties": {
                "test_event_code": {"type": "string", "description": "Test event code from Facebook Events Manager"},
                "event_name": {"type": "string", "description": "Event type to test: Purchase, Lead, PageView", "default": "Purchase"},
            },
            "required": ["test_event_code"],
        },
    },
]


def _sha256(value: str) -> str:
    """SHA256 hash a string for PII fields."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


async def _send_events(client: httpx.AsyncClient, pixel_id: str, access_token: str, events: list[dict[str, Any]], test_code: str | None = None) -> dict[str, Any]:
    """Send events to the Facebook Conversions API."""
    base_url = f"https://graph.facebook.com/v18.0/{pixel_id}/events"
    payload: dict[str, Any] = {
        "data": events,
        "access_token": access_token,
    }
    if test_code:
        payload["test_event_code"] = test_code
    r = await client.post(base_url, json=payload)
    r.raise_for_status()
    return r.json()


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    pixel_id = os.getenv("FACEBOOK_PIXEL_ID", "")
    access_token = os.getenv("FACEBOOK_ACCESS_TOKEN", "")
    if not pixel_id:
        return {"error": "FACEBOOK_PIXEL_ID not configured"}
    if not access_token:
        return {"error": "FACEBOOK_ACCESS_TOKEN not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "facebook_conversions_send_event":
                event: dict[str, Any] = {
                    "event_name": arguments["event_name"],
                    "event_time": arguments.get("event_time", int(time.time())),
                }
                if "event_source_url" in arguments:
                    event["event_source_url"] = arguments["event_source_url"]
                if "user_data" in arguments:
                    event["user_data"] = arguments["user_data"]
                if "custom_data" in arguments:
                    event["custom_data"] = arguments["custom_data"]
                return await _send_events(client, pixel_id, access_token, [event], arguments.get("test_event_code"))

            elif tool_name == "facebook_conversions_send_purchase_event":
                user_data: dict[str, Any] = {}
                if "user_email" in arguments:
                    user_data["em"] = [_sha256(arguments["user_email"])]
                if "user_phone" in arguments:
                    user_data["ph"] = [_sha256(arguments["user_phone"])]
                custom_data: dict[str, Any] = {
                    "value": arguments["value"],
                    "currency": arguments.get("currency", "USD"),
                }
                if "content_ids" in arguments:
                    custom_data["content_ids"] = arguments["content_ids"]
                if "order_id" in arguments:
                    custom_data["order_id"] = arguments["order_id"]
                event = {
                    "event_name": "Purchase",
                    "event_time": int(time.time()),
                    "user_data": user_data,
                    "custom_data": custom_data,
                }
                if "event_source_url" in arguments:
                    event["event_source_url"] = arguments["event_source_url"]
                return await _send_events(client, pixel_id, access_token, [event], arguments.get("test_event_code"))

            elif tool_name == "facebook_conversions_send_lead_event":
                user_data = {}
                if "user_email" in arguments:
                    user_data["em"] = [_sha256(arguments["user_email"])]
                if "user_phone" in arguments:
                    user_data["ph"] = [_sha256(arguments["user_phone"])]
                custom_data = {}
                if "lead_id" in arguments:
                    custom_data["lead_id"] = arguments["lead_id"]
                event = {
                    "event_name": "Lead",
                    "event_time": int(time.time()),
                    "user_data": user_data,
                }
                if custom_data:
                    event["custom_data"] = custom_data
                if "event_source_url" in arguments:
                    event["event_source_url"] = arguments["event_source_url"]
                return await _send_events(client, pixel_id, access_token, [event], arguments.get("test_event_code"))

            elif tool_name == "facebook_conversions_send_page_view":
                user_data = {}
                if "user_email" in arguments:
                    user_data["em"] = [_sha256(arguments["user_email"])]
                if "client_ip_address" in arguments:
                    user_data["client_ip_address"] = arguments["client_ip_address"]
                if "client_user_agent" in arguments:
                    user_data["client_user_agent"] = arguments["client_user_agent"]
                event = {
                    "event_name": "PageView",
                    "event_time": int(time.time()),
                    "event_source_url": arguments["event_source_url"],
                    "user_data": user_data,
                }
                return await _send_events(client, pixel_id, access_token, [event], arguments.get("test_event_code"))

            elif tool_name == "facebook_conversions_send_custom_event":
                user_data = {}
                if "user_email" in arguments:
                    user_data["em"] = [_sha256(arguments["user_email"])]
                custom_data = {}
                if "value" in arguments:
                    custom_data["value"] = arguments["value"]
                if "currency" in arguments:
                    custom_data["currency"] = arguments["currency"]
                if "custom_properties" in arguments:
                    custom_data.update(arguments["custom_properties"])
                event = {
                    "event_name": arguments["event_name"],
                    "event_time": int(time.time()),
                    "user_data": user_data,
                }
                if "event_source_url" in arguments:
                    event["event_source_url"] = arguments["event_source_url"]
                if custom_data:
                    event["custom_data"] = custom_data
                return await _send_events(client, pixel_id, access_token, [event], arguments.get("test_event_code"))

            elif tool_name == "facebook_conversions_test_event":
                event = {
                    "event_name": arguments.get("event_name", "Purchase"),
                    "event_time": int(time.time()),
                    "user_data": {"em": [_sha256("test@example.com")]},
                    "custom_data": {"value": 1.0, "currency": "USD"},
                }
                return await _send_events(
                    client, pixel_id, access_token, [event],
                    arguments["test_event_code"],
                )

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("facebook_conversions_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
