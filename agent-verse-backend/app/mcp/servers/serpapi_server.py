"""SerpAPI MCP server — Google Search, Images, News, and more.

Environment:
  SERPAPI_API_KEY: SerpAPI key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

SERPAPI_BASE = "https://serpapi.com"

TOOL_DEFINITIONS = [
    {
        "name": "serpapi_google_search",
        "description": "Search Google via SerpAPI with full structured results",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "num": {"type": "integer", "default": 10, "description": "Number of results (1–100)"},
                "location": {"type": "string", "description": "Location for results, e.g. 'New York, NY'"},
                "hl": {"type": "string", "default": "en", "description": "Language code"},
                "gl": {"type": "string", "default": "us", "description": "Country code"},
                "start": {"type": "integer", "default": 0, "description": "Result offset for pagination"},
                "safe": {"type": "string", "enum": ["active", "off"], "default": "off"},
                "time_period": {
                    "type": "string",
                    "enum": ["last_hour", "last_day", "last_week", "last_month", "last_year"],
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "serpapi_google_news",
        "description": "Search Google News via SerpAPI",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "num": {"type": "integer", "default": 10},
                "hl": {"type": "string", "default": "en"},
                "gl": {"type": "string", "default": "us"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "serpapi_google_images",
        "description": "Search Google Images via SerpAPI",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "num": {"type": "integer", "default": 10},
                "hl": {"type": "string", "default": "en"},
                "image_size": {"type": "string", "enum": ["icon", "small", "medium", "large", "xlarge", "xxlarge"]},
                "image_type": {"type": "string", "enum": ["clipart", "face", "lineart", "stock", "photo", "animated"]},
            },
            "required": ["query"],
        },
    },
    {
        "name": "serpapi_google_shopping",
        "description": "Search Google Shopping via SerpAPI",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "num": {"type": "integer", "default": 10},
                "location": {"type": "string"},
                "price_min": {"type": "number"},
                "price_max": {"type": "number"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "serpapi_google_maps",
        "description": "Search Google Maps via SerpAPI for local business results",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "location": {"type": "string", "description": "Location to search around"},
                "ll": {
                    "type": "string",
                    "description": "Latitude/longitude string, e.g. '@40.7128,-74.0060,15z'",
                },
            },
            "required": ["query"],
        },
    },
]


def _base_params() -> dict[str, Any]:
    return {"api_key": os.getenv("SERPAPI_API_KEY", ""), "output": "json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("SERPAPI_API_KEY", "")
    if not key:
        return {"error": "SERPAPI_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "serpapi_google_search":
                params = {
                    **_base_params(),
                    "engine": "google",
                    "q": arguments["query"],
                    "num": arguments.get("num", 10),
                    "hl": arguments.get("hl", "en"),
                    "gl": arguments.get("gl", "us"),
                    "start": arguments.get("start", 0),
                    "safe": arguments.get("safe", "off"),
                }
                if loc := arguments.get("location"):
                    params["location"] = loc
                if tp := arguments.get("time_period"):
                    mapping = {
                        "last_hour": "qdr:h",
                        "last_day": "qdr:d",
                        "last_week": "qdr:w",
                        "last_month": "qdr:m",
                        "last_year": "qdr:y",
                    }
                    params["tbs"] = mapping.get(tp, "")
                r = await c.get(f"{SERPAPI_BASE}/search", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "organic_results": data.get("organic_results", []),
                    "answer_box": data.get("answer_box"),
                    "knowledge_graph": data.get("knowledge_graph"),
                    "related_searches": data.get("related_searches", []),
                }

            elif tool_name == "serpapi_google_news":
                params = {
                    **_base_params(),
                    "engine": "google",
                    "q": arguments["query"],
                    "tbm": "nws",
                    "num": arguments.get("num", 10),
                    "hl": arguments.get("hl", "en"),
                    "gl": arguments.get("gl", "us"),
                }
                r = await c.get(f"{SERPAPI_BASE}/search", params=params)
                r.raise_for_status()
                data = r.json()
                return {"news_results": data.get("news_results", [])}

            elif tool_name == "serpapi_google_images":
                params = {
                    **_base_params(),
                    "engine": "google_images",
                    "q": arguments["query"],
                    "num": arguments.get("num", 10),
                    "hl": arguments.get("hl", "en"),
                }
                if size := arguments.get("image_size"):
                    params["imgsz"] = size
                if itype := arguments.get("image_type"):
                    params["imgtype"] = itype
                r = await c.get(f"{SERPAPI_BASE}/search", params=params)
                r.raise_for_status()
                data = r.json()
                return {"images_results": data.get("images_results", [])}

            elif tool_name == "serpapi_google_shopping":
                params = {
                    **_base_params(),
                    "engine": "google_shopping",
                    "q": arguments["query"],
                    "num": arguments.get("num", 10),
                }
                if loc := arguments.get("location"):
                    params["location"] = loc
                if pmin := arguments.get("price_min"):
                    params["price_min"] = pmin
                if pmax := arguments.get("price_max"):
                    params["price_max"] = pmax
                r = await c.get(f"{SERPAPI_BASE}/search", params=params)
                r.raise_for_status()
                data = r.json()
                return {"shopping_results": data.get("shopping_results", [])}

            elif tool_name == "serpapi_google_maps":
                params = {
                    **_base_params(),
                    "engine": "google_maps",
                    "q": arguments["query"],
                    "type": "search",
                }
                if loc := arguments.get("location"):
                    params["location"] = loc
                if ll := arguments.get("ll"):
                    params["ll"] = ll
                r = await c.get(f"{SERPAPI_BASE}/search", params=params)
                r.raise_for_status()
                data = r.json()
                return {"local_results": data.get("local_results", [])}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("serpapi_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
