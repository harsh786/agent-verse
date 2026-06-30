"""Home Assistant MCP server — IoT smart home control and automation.

Environment:
  HOME_ASSISTANT_TOKEN: Long-lived access token for Home Assistant API
  HOME_ASSISTANT_URL: Base URL of the Home Assistant instance (e.g. http://homeassistant.local:8123)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    url = os.getenv("HOME_ASSISTANT_URL", "http://homeassistant.local:8123")
    return f"{url.rstrip('/')}/api"


TOOL_DEFINITIONS = [
    {
        "name": "homeassistant_list_entities",
        "description": "List all entities (devices and sensors) in Home Assistant",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Filter by domain: light, switch, sensor, climate, etc."},
            },
        },
    },
    {
        "name": "homeassistant_get_entity_state",
        "description": "Get the current state and attributes of a specific entity",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity ID (e.g. light.living_room)"},
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "homeassistant_call_service",
        "description": "Call a Home Assistant service to control a device or automation",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Service domain (e.g. light, switch, climate)"},
                "service": {"type": "string", "description": "Service name (e.g. turn_on, turn_off, toggle)"},
                "entity_id": {"type": "string", "description": "Target entity ID"},
                "service_data": {"type": "object", "description": "Additional service parameters"},
            },
            "required": ["domain", "service"],
        },
    },
    {
        "name": "homeassistant_list_automations",
        "description": "List all automations configured in Home Assistant",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "homeassistant_trigger_automation",
        "description": "Manually trigger a specific Home Assistant automation",
        "parameters": {
            "type": "object",
            "properties": {
                "automation_id": {"type": "string", "description": "Automation entity ID (e.g. automation.morning_lights)"},
            },
            "required": ["automation_id"],
        },
    },
    {
        "name": "homeassistant_get_history",
        "description": "Get historical state changes for an entity over a time period",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity ID to get history for"},
                "start_time": {"type": "string", "description": "Start time in ISO 8601 format"},
                "end_time": {"type": "string", "description": "End time in ISO 8601 format"},
            },
            "required": ["entity_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    token = os.getenv("HOME_ASSISTANT_TOKEN", "")
    ha_url = os.getenv("HOME_ASSISTANT_URL", "")
    if not token:
        return {"error": "HOME_ASSISTANT_TOKEN not configured"}
    if not ha_url:
        return {"error": "HOME_ASSISTANT_URL not configured"}

    base_url = _base_url()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "homeassistant_list_entities":
                r = await client.get(f"{base_url}/states", headers=headers)
                r.raise_for_status()
                states = r.json()
                if "domain" in arguments:
                    domain = arguments["domain"]
                    states = [s for s in states if s.get("entity_id", "").startswith(f"{domain}.")]
                return states

            if tool_name == "homeassistant_get_entity_state":
                r = await client.get(
                    f"{base_url}/states/{arguments['entity_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "homeassistant_call_service":
                service_data = arguments.get("service_data", {})
                if "entity_id" in arguments:
                    service_data["entity_id"] = arguments["entity_id"]
                r = await client.post(
                    f"{base_url}/services/{arguments['domain']}/{arguments['service']}",
                    headers=headers,
                    json=service_data,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "homeassistant_list_automations":
                r = await client.get(f"{base_url}/states", headers=headers)
                r.raise_for_status()
                return [s for s in r.json() if s.get("entity_id", "").startswith("automation.")]

            if tool_name == "homeassistant_trigger_automation":
                r = await client.post(
                    f"{base_url}/services/automation/trigger",
                    headers=headers,
                    json={"entity_id": arguments["automation_id"]},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "homeassistant_get_history":
                params: dict[str, Any] = {"filter_entity_id": arguments["entity_id"]}
                if "end_time" in arguments:
                    params["end_time"] = arguments["end_time"]
                start = arguments.get("start_time", "")
                url = f"{base_url}/history/period/{start}" if start else f"{base_url}/history/period"
                r = await client.get(url, headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
