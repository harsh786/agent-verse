"""Fitbit MCP server — fitness tracking, health data, and device management.

Environment:
  FITBIT_ACCESS_TOKEN: Fitbit OAuth2 access token with appropriate scopes
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.fitbit.com/1"

TOOL_DEFINITIONS = [
    {
        "name": "fitbit_get_activity_summary",
        "description": "Get daily activity summary (steps, calories, distance) for a date",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format (or 'today')"},
                "user_id": {"type": "string", "description": "User ID (default: '-' for authenticated user)"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "fitbit_get_heart_rate",
        "description": "Get heart rate time series data for a date or date range",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "period": {"type": "string", "description": "Period: 1d, 7d, 30d, 3m, 6m, 1y"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "fitbit_get_sleep_data",
        "description": "Get sleep data and sleep stages for a specific date",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "fitbit_get_body_measurements",
        "description": "Get body measurements (weight, BMI, fat) for a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "resource": {"type": "string", "description": "Measurement type: weight, fat, bmi"},
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["resource", "start_date"],
        },
    },
    {
        "name": "fitbit_list_devices",
        "description": "List Fitbit devices registered to the user account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "fitbit_get_nutrition",
        "description": "Get food log and nutrition data for a specific date",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
            },
            "required": ["date"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("FITBIT_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "FITBIT_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}"}
    user_id = arguments.get("user_id", "-")
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "fitbit_get_activity_summary":
                r = await client.get(
                    f"{BASE_URL}/user/{user_id}/activities/date/{arguments['date']}.json",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "fitbit_get_heart_rate":
                period = arguments.get("period", "1d")
                r = await client.get(
                    f"{BASE_URL}/user/{user_id}/activities/heart/date/{arguments['date']}/{period}.json",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "fitbit_get_sleep_data":
                r = await client.get(
                    f"{BASE_URL}/user/{user_id}/sleep/date/{arguments['date']}.json",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "fitbit_get_body_measurements":
                resource = arguments["resource"]
                start = arguments["start_date"]
                end = arguments.get("end_date", start)
                r = await client.get(
                    f"{BASE_URL}/user/{user_id}/body/{resource}/date/{start}/{end}.json",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "fitbit_list_devices":
                r = await client.get(f"{BASE_URL}/user/{user_id}/devices.json", headers=headers)
                r.raise_for_status()
                return r.json()

            if tool_name == "fitbit_get_nutrition":
                r = await client.get(
                    f"{BASE_URL}/user/{user_id}/foods/log/date/{arguments['date']}.json",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
