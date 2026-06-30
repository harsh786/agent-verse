"""Beds24 MCP server — vacation rental property management and bookings.

Environment:
  BEDS24_API_KEY: Beds24 API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.beds24.com/api/v2"

TOOL_DEFINITIONS = [
    {
        "name": "beds24_get_availability",
        "description": "Get availability calendar for a Beds24 property",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "integer", "description": "Beds24 property ID"},
                "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
            },
            "required": ["property_id", "start_date", "end_date"],
        },
    },
    {
        "name": "beds24_create_booking",
        "description": "Create a new booking reservation in Beds24",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "integer", "description": "Property ID"},
                "room_id": {"type": "integer", "description": "Room/unit ID"},
                "first_night": {"type": "string", "description": "First night in YYYY-MM-DD"},
                "last_night": {"type": "string", "description": "Last night in YYYY-MM-DD"},
                "num_adults": {"type": "integer", "description": "Number of adults"},
                "guest_first_name": {"type": "string", "description": "Guest first name"},
                "guest_last_name": {"type": "string", "description": "Guest last name"},
                "guest_email": {"type": "string", "description": "Guest email address"},
            },
            "required": ["property_id", "first_night", "last_night"],
        },
    },
    {
        "name": "beds24_get_booking",
        "description": "Get details of a specific Beds24 booking",
        "parameters": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "integer", "description": "Beds24 booking ID"},
            },
            "required": ["booking_id"],
        },
    },
    {
        "name": "beds24_list_properties",
        "description": "List all properties in the Beds24 account",
        "parameters": {
            "type": "object",
            "properties": {
                "include_rooms": {"type": "boolean", "description": "Include room details"},
            },
        },
    },
    {
        "name": "beds24_update_booking",
        "description": "Update an existing Beds24 booking (dates, guests, or status)",
        "parameters": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "integer", "description": "Booking ID to update"},
                "status": {"type": "string", "description": "New booking status"},
                "first_night": {"type": "string", "description": "Updated first night"},
                "last_night": {"type": "string", "description": "Updated last night"},
            },
            "required": ["booking_id"],
        },
    },
    {
        "name": "beds24_get_stats",
        "description": "Get occupancy and revenue statistics for a property",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "integer", "description": "Property ID"},
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("BEDS24_API_KEY", "")
    if not api_key:
        return {"error": "BEDS24_API_KEY not configured"}

    headers = {"token": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "beds24_get_availability":
                r = await client.get(
                    f"{BASE_URL}/inventory/rooms/calendar",
                    headers=headers,
                    params={
                        "propertyId": arguments["property_id"],
                        "startDate": arguments["start_date"],
                        "endDate": arguments["end_date"],
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "beds24_create_booking":
                payload: dict[str, Any] = {
                    "propertyId": arguments["property_id"],
                    "firstNight": arguments["first_night"],
                    "lastNight": arguments["last_night"],
                    "numAdult": arguments.get("num_adults", 1),
                }
                if "room_id" in arguments:
                    payload["roomId"] = arguments["room_id"]
                if "guest_first_name" in arguments:
                    payload["guestFirstName"] = arguments["guest_first_name"]
                if "guest_last_name" in arguments:
                    payload["guestLastName"] = arguments["guest_last_name"]
                if "guest_email" in arguments:
                    payload["guestEmail"] = arguments["guest_email"]
                r = await client.post(f"{BASE_URL}/bookings", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "beds24_get_booking":
                r = await client.get(
                    f"{BASE_URL}/bookings/{arguments['booking_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "beds24_list_properties":
                params: dict[str, Any] = {}
                if arguments.get("include_rooms"):
                    params["includeRooms"] = "1"
                r = await client.get(f"{BASE_URL}/properties", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "beds24_update_booking":
                booking_id = arguments["booking_id"]
                payload = {k: v for k, v in arguments.items() if k != "booking_id" and v is not None}
                r = await client.put(
                    f"{BASE_URL}/bookings/{booking_id}",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "beds24_get_stats":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/bookings/stats", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
