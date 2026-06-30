"""Flexport MCP server — supply chain logistics, shipments, and freight management.

Environment:
  FLEXPORT_API_KEY: Flexport API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.flexport.com"

TOOL_DEFINITIONS = [
    {
        "name": "flexport_list_shipments",
        "description": "List all shipments in the Flexport account with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "per": {"type": "integer", "description": "Results per page"},
                "page": {"type": "integer", "description": "Page number"},
                "status": {"type": "string", "description": "Filter by status"},
                "transport_mode": {"type": "string", "description": "Filter by mode: ocean, air, truck"},
            },
        },
    },
    {
        "name": "flexport_get_shipment",
        "description": "Get detailed information about a specific Flexport shipment",
        "parameters": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "integer", "description": "Flexport shipment ID"},
            },
            "required": ["shipment_id"],
        },
    },
    {
        "name": "flexport_create_shipment",
        "description": "Create a new freight shipment request in Flexport",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Shipment name or reference"},
                "transport_mode": {"type": "string", "description": "Transport mode: ocean, air, truck"},
                "origin": {"type": "object", "description": "Origin location details"},
                "destination": {"type": "object", "description": "Destination location details"},
                "cargo": {"type": "array", "description": "List of cargo items", "items": {"type": "object"}},
            },
            "required": ["transport_mode"],
        },
    },
    {
        "name": "flexport_list_documents",
        "description": "List documents associated with a Flexport shipment",
        "parameters": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "integer", "description": "Shipment ID to get documents for"},
                "per": {"type": "integer", "description": "Results per page"},
                "page": {"type": "integer", "description": "Page number"},
            },
            "required": ["shipment_id"],
        },
    },
    {
        "name": "flexport_track_shipment",
        "description": "Get real-time tracking milestones and events for a shipment",
        "parameters": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "integer", "description": "Shipment ID to track"},
            },
            "required": ["shipment_id"],
        },
    },
    {
        "name": "flexport_get_quotes",
        "description": "Get freight rate quotes for a shipment route",
        "parameters": {
            "type": "object",
            "properties": {
                "origin_port": {"type": "string", "description": "Origin port UNLOCODE (e.g. CNSHA)"},
                "destination_port": {"type": "string", "description": "Destination port UNLOCODE (e.g. USLAX)"},
                "transport_mode": {"type": "string", "description": "Transport mode: ocean, air"},
                "cargo_type": {"type": "string", "description": "Cargo type: FCL, LCL, AIR"},
                "container_size": {"type": "string", "description": "Container size: 20, 40, 40HC"},
            },
            "required": ["origin_port", "destination_port"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("FLEXPORT_API_KEY", "")
    if not api_key:
        return {"error": "FLEXPORT_API_KEY not configured"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Flexport-Version": "3",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "flexport_list_shipments":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/shipments", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "flexport_get_shipment":
                r = await client.get(
                    f"{BASE_URL}/shipments/{arguments['shipment_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "flexport_create_shipment":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(f"{BASE_URL}/shipments", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "flexport_list_documents":
                shipment_id = arguments["shipment_id"]
                params = {k: v for k, v in arguments.items() if k != "shipment_id" and v is not None}
                r = await client.get(
                    f"{BASE_URL}/shipments/{shipment_id}/documents",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "flexport_track_shipment":
                r = await client.get(
                    f"{BASE_URL}/shipments/{arguments['shipment_id']}/legs",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "flexport_get_quotes":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(f"{BASE_URL}/quotes", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
