"""Uber Rides MCP server — fare/ETA estimates, ride requests, and ride status.

Environment:
  UBER_ACCESS_TOKEN: OAuth2 bearer token for the Uber Rides API
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

UBER_BASE = "https://api.uber.com/v1.2"

_LATLNG = {
    "type": "object",
    "properties": {
        "lat": {"type": "number", "description": "Latitude"},
        "lng": {"type": "number", "description": "Longitude"},
    },
    "required": ["lat", "lng"],
}


TOOL_DEFINITIONS = [
    {
        "name": "uber_estimate_ride",
        "description": "Estimate fare and ETA for a ride between a pickup and dropoff point",
        "parameters": {
            "type": "object",
            "properties": {
                "pickup": {**_LATLNG, "description": "Pickup location {lat, lng}"},
                "dropoff": {**_LATLNG, "description": "Dropoff location {lat, lng}"},
            },
            "required": ["pickup", "dropoff"],
        },
    },
    {
        "name": "uber_request_ride",
        "description": "Request (book) a ride between a pickup and dropoff point",
        "parameters": {
            "type": "object",
            "properties": {
                "pickup": {**_LATLNG, "description": "Pickup location {lat, lng}"},
                "dropoff": {**_LATLNG, "description": "Dropoff location {lat, lng}"},
                "product_id": {
                    "type": "string",
                    "description": "Optional Uber product/ride-type ID (e.g. uberX)",
                },
                "fare_id": {
                    "type": "string",
                    "description": "Optional upfront fare ID from an estimate",
                },
            },
            "required": ["pickup", "dropoff"],
        },
    },
    {
        "name": "uber_ride_status",
        "description": "Get the current status of a previously requested ride",
        "parameters": {
            "type": "object",
            "properties": {
                "ride_id": {"type": "string", "description": "The ride request ID"},
            },
            "required": ["ride_id"],
        },
    },
]


def _token() -> str:
    return os.getenv("UBER_ACCESS_TOKEN", "")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = _token()
    if not token:
        return {"error": "UBER_ACCESS_TOKEN required"}

    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "uber_estimate_ride":
                pickup = arguments["pickup"]
                dropoff = arguments["dropoff"]
                price_params = {
                    "start_latitude": pickup["lat"],
                    "start_longitude": pickup["lng"],
                    "end_latitude": dropoff["lat"],
                    "end_longitude": dropoff["lng"],
                }
                pr = await c.get(
                    f"{UBER_BASE}/estimates/price", headers=hdrs, params=price_params
                )
                pr.raise_for_status()
                price_data = pr.json()
                tr = await c.get(
                    f"{UBER_BASE}/estimates/time",
                    headers=hdrs,
                    params={
                        "start_latitude": pickup["lat"],
                        "start_longitude": pickup["lng"],
                    },
                )
                tr.raise_for_status()
                time_data = tr.json()
                times = {
                    t.get("product_id"): t.get("estimate") for t in time_data.get("times", [])
                }
                return {
                    "estimates": [
                        {
                            "product": p.get("display_name"),
                            "product_id": p.get("product_id"),
                            "fare": p.get("estimate"),
                            "low_estimate": p.get("low_estimate"),
                            "high_estimate": p.get("high_estimate"),
                            "currency": p.get("currency_code"),
                            "distance": p.get("distance"),
                            "duration": p.get("duration"),
                            "pickup_eta_seconds": times.get(p.get("product_id")),
                        }
                        for p in price_data.get("prices", [])
                    ]
                }

            elif tool_name == "uber_request_ride":
                pickup = arguments["pickup"]
                dropoff = arguments["dropoff"]
                body: dict[str, Any] = {
                    "start_latitude": pickup["lat"],
                    "start_longitude": pickup["lng"],
                    "end_latitude": dropoff["lat"],
                    "end_longitude": dropoff["lng"],
                }
                if pid := arguments.get("product_id"):
                    body["product_id"] = pid
                if fid := arguments.get("fare_id"):
                    body["fare_id"] = fid
                r = await c.post(f"{UBER_BASE}/requests", headers=hdrs, json=body)
                r.raise_for_status()
                data = r.json()
                return {
                    "ride_id": data.get("request_id"),
                    "status": data.get("status"),
                    "product_id": data.get("product_id"),
                    "eta": data.get("eta"),
                    "surge_multiplier": data.get("surge_multiplier"),
                }

            elif tool_name == "uber_ride_status":
                ride_id = arguments["ride_id"]
                r = await c.get(f"{UBER_BASE}/requests/{ride_id}", headers=hdrs)
                r.raise_for_status()
                data = r.json()
                return {
                    "ride_id": data.get("request_id", ride_id),
                    "status": data.get("status"),
                    "eta": data.get("eta"),
                    "location": data.get("location"),
                    "driver": data.get("driver"),
                    "vehicle": data.get("vehicle"),
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("uber_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
