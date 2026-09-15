"""Google Maps MCP server — geocoding, directions, places, and distance matrix.

Environment:
  GOOGLE_MAPS_API_KEY: Google Maps Platform API key (used as the `key` query param)
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

MAPS_BASE = "https://maps.googleapis.com/maps/api"


TOOL_DEFINITIONS = [
    {
        "name": "maps_geocode",
        "description": "Geocode a street address into latitude/longitude coordinates",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Street address or place name"},
                "region": {
                    "type": "string",
                    "description": "Optional ccTLD region bias (e.g. 'us', 'uk')",
                },
            },
            "required": ["address"],
        },
    },
    {
        "name": "maps_directions",
        "description": "Get a route summary (distance, duration, steps) between two points",
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Origin address or 'lat,lng'"},
                "destination": {
                    "type": "string",
                    "description": "Destination address or 'lat,lng'",
                },
                "mode": {
                    "type": "string",
                    "enum": ["driving", "walking", "bicycling", "transit"],
                    "default": "driving",
                    "description": "Travel mode",
                },
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "maps_place_search",
        "description": "Search for places matching a free-text query, optionally near a location",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text search query"},
                "near": {
                    "type": "string",
                    "description": "Optional 'lat,lng' to bias results around",
                },
                "radius": {
                    "type": "integer",
                    "description": "Optional search radius in meters (used with 'near')",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "maps_distance_matrix",
        "description": "Compute travel distance and time for a matrix of origins and destinations",
        "parameters": {
            "type": "object",
            "properties": {
                "origins": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Origin addresses or 'lat,lng' strings",
                },
                "destinations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Destination addresses or 'lat,lng' strings",
                },
                "mode": {
                    "type": "string",
                    "enum": ["driving", "walking", "bicycling", "transit"],
                    "default": "driving",
                },
            },
            "required": ["origins", "destinations"],
        },
    },
]


def _api_key() -> str:
    return os.getenv("GOOGLE_MAPS_API_KEY", "")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = _api_key()
    if not key:
        return {"error": "GOOGLE_MAPS_API_KEY required"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "maps_geocode":
                params: dict[str, Any] = {"address": arguments["address"], "key": key}
                if region := arguments.get("region"):
                    params["region"] = region
                r = await c.get(f"{MAPS_BASE}/geocode/json", params=params)
                r.raise_for_status()
                data = r.json()
                results = data.get("results", [])
                if not results:
                    return {"status": data.get("status", "ZERO_RESULTS"), "results": []}
                first = results[0]
                loc = first.get("geometry", {}).get("location", {})
                return {
                    "status": data.get("status"),
                    "formatted_address": first.get("formatted_address"),
                    "lat": loc.get("lat"),
                    "lng": loc.get("lng"),
                    "place_id": first.get("place_id"),
                }

            elif tool_name == "maps_directions":
                params = {
                    "origin": arguments["origin"],
                    "destination": arguments["destination"],
                    "mode": arguments.get("mode", "driving"),
                    "key": key,
                }
                r = await c.get(f"{MAPS_BASE}/directions/json", params=params)
                r.raise_for_status()
                data = r.json()
                routes = data.get("routes", [])
                if not routes:
                    return {"status": data.get("status", "ZERO_RESULTS"), "routes": []}
                leg = routes[0].get("legs", [{}])[0]
                return {
                    "status": data.get("status"),
                    "summary": routes[0].get("summary", ""),
                    "distance": leg.get("distance", {}).get("text"),
                    "duration": leg.get("duration", {}).get("text"),
                    "start_address": leg.get("start_address"),
                    "end_address": leg.get("end_address"),
                    "steps": [
                        {
                            "instruction": s.get("html_instructions", ""),
                            "distance": s.get("distance", {}).get("text"),
                            "duration": s.get("duration", {}).get("text"),
                        }
                        for s in leg.get("steps", [])
                    ],
                }

            elif tool_name == "maps_place_search":
                params = {"query": arguments["query"], "key": key}
                if near := arguments.get("near"):
                    params["location"] = near
                    params["radius"] = arguments.get("radius", 5000)
                r = await c.get(f"{MAPS_BASE}/place/textsearch/json", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "status": data.get("status"),
                    "places": [
                        {
                            "name": p.get("name"),
                            "address": p.get("formatted_address"),
                            "lat": p.get("geometry", {}).get("location", {}).get("lat"),
                            "lng": p.get("geometry", {}).get("location", {}).get("lng"),
                            "rating": p.get("rating"),
                            "place_id": p.get("place_id"),
                        }
                        for p in data.get("results", [])
                    ],
                }

            elif tool_name == "maps_distance_matrix":
                params = {
                    "origins": "|".join(arguments["origins"]),
                    "destinations": "|".join(arguments["destinations"]),
                    "mode": arguments.get("mode", "driving"),
                    "key": key,
                }
                r = await c.get(f"{MAPS_BASE}/distancematrix/json", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "status": data.get("status"),
                    "origin_addresses": data.get("origin_addresses", []),
                    "destination_addresses": data.get("destination_addresses", []),
                    "rows": [
                        {
                            "elements": [
                                {
                                    "status": el.get("status"),
                                    "distance": el.get("distance", {}).get("text"),
                                    "duration": el.get("duration", {}).get("text"),
                                }
                                for el in row.get("elements", [])
                            ]
                        }
                        for row in data.get("rows", [])
                    ],
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("google_maps_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
