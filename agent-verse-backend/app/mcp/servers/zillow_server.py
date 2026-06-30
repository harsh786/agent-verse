"""Zillow MCP server — real estate search, property details, and market estimates.

Environment:
  ZILLOW_API_KEY: Zillow Bridge API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.bridgedataoutput.com/api/v2"

TOOL_DEFINITIONS = [
    {
        "name": "zillow_search_properties",
        "description": "Search for properties by location, price range, and property type",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Street address or area to search"},
                "city": {"type": "string", "description": "City name"},
                "state": {"type": "string", "description": "State code (e.g. CA, NY)"},
                "zipcode": {"type": "string", "description": "ZIP code"},
                "min_price": {"type": "number", "description": "Minimum listing price"},
                "max_price": {"type": "number", "description": "Maximum listing price"},
                "bedrooms": {"type": "integer", "description": "Minimum number of bedrooms"},
                "bathrooms": {"type": "number", "description": "Minimum number of bathrooms"},
                "limit": {"type": "integer", "description": "Maximum results to return"},
            },
        },
    },
    {
        "name": "zillow_get_property_details",
        "description": "Get comprehensive details for a specific property listing",
        "parameters": {
            "type": "object",
            "properties": {
                "zpid": {"type": "string", "description": "Zillow Property ID (ZPID)"},
            },
            "required": ["zpid"],
        },
    },
    {
        "name": "zillow_get_zestimate",
        "description": "Get the Zestimate (Zillow estimated market value) for a property",
        "parameters": {
            "type": "object",
            "properties": {
                "zpid": {"type": "string", "description": "Zillow Property ID"},
                "address": {"type": "string", "description": "Full property address as alternative to ZPID"},
            },
        },
    },
    {
        "name": "zillow_get_neighborhood_info",
        "description": "Get neighborhood statistics including school ratings and demographics",
        "parameters": {
            "type": "object",
            "properties": {
                "zipcode": {"type": "string", "description": "ZIP code of the neighborhood"},
                "city": {"type": "string", "description": "City name"},
                "state": {"type": "string", "description": "State code"},
            },
        },
    },
    {
        "name": "zillow_list_sold_properties",
        "description": "Find recently sold properties in an area for comparable market analysis",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City to search in"},
                "state": {"type": "string", "description": "State code"},
                "zipcode": {"type": "string", "description": "ZIP code"},
                "days_sold": {"type": "integer", "description": "Look back N days for sold properties"},
                "limit": {"type": "integer", "description": "Maximum results"},
            },
        },
    },
    {
        "name": "zillow_get_rent_estimates",
        "description": "Get Rent Zestimate (rental market value estimates) for a property or area",
        "parameters": {
            "type": "object",
            "properties": {
                "zpid": {"type": "string", "description": "Zillow Property ID"},
                "zipcode": {"type": "string", "description": "ZIP code for area rent estimates"},
                "bedrooms": {"type": "integer", "description": "Number of bedrooms for area estimates"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("ZILLOW_API_KEY", "")
    if not api_key:
        return {"error": "ZILLOW_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            base_params: dict[str, Any] = {"access_token": api_key}

            if tool_name == "zillow_search_properties":
                params = {**base_params, **{k: v for k, v in arguments.items() if v is not None}}
                r = await client.get(f"{BASE_URL}/pub/listings", params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "zillow_get_property_details":
                params = {**base_params, "zpid": arguments["zpid"]}
                r = await client.get(f"{BASE_URL}/pub/listings/{arguments['zpid']}", params=base_params)
                r.raise_for_status()
                return r.json()

            if tool_name == "zillow_get_zestimate":
                params = {**base_params, **{k: v for k, v in arguments.items() if v is not None}}
                r = await client.get(f"{BASE_URL}/pub/zestimates", params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "zillow_get_neighborhood_info":
                params = {**base_params, **{k: v for k, v in arguments.items() if v is not None}}
                r = await client.get(f"{BASE_URL}/pub/neighborhoods", params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "zillow_list_sold_properties":
                params = {**base_params, "status": "sold", **{k: v for k, v in arguments.items() if v is not None}}
                r = await client.get(f"{BASE_URL}/pub/listings", params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "zillow_get_rent_estimates":
                params = {**base_params, **{k: v for k, v in arguments.items() if v is not None}}
                r = await client.get(f"{BASE_URL}/pub/rentzestimates", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
