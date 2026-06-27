"""Brave Search MCP server — privacy-focused web and news search.

Environment:
  BRAVE_SEARCH_API_KEY: Brave Search API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BRAVE_SEARCH_BASE = "https://api.search.brave.com/res/v1"

TOOL_DEFINITIONS = [
    {
        "name": "brave_web_search",
        "description": "Search the web using Brave Search API (privacy-focused)",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "count": {"type": "integer", "default": 10, "description": "1–20"},
                "offset": {"type": "integer", "default": 0, "description": "Pagination offset (0–9)"},
                "country": {"type": "string", "description": "ISO 3166-1 alpha-2 country code, e.g. 'US'"},
                "search_lang": {"type": "string", "description": "Language code, e.g. 'en'"},
                "safesearch": {
                    "type": "string",
                    "enum": ["off", "moderate", "strict"],
                    "default": "moderate",
                },
                "freshness": {
                    "type": "string",
                    "description": "Recency filter: 'pd' (past day), 'pw' (past week), 'pm' (past month), 'py' (past year)",
                },
                "result_filter": {
                    "type": "string",
                    "description": "Filter to: 'web', 'news', 'videos', 'images', 'discussions'",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "brave_news_search",
        "description": "Search for news using Brave Search API",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "count": {"type": "integer", "default": 10},
                "country": {"type": "string"},
                "search_lang": {"type": "string", "default": "en"},
                "freshness": {"type": "string", "description": "pd, pw, pm, py"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "brave_local_search",
        "description": "Search for local businesses and points of interest using Brave",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "count": {"type": "integer", "default": 5},
                "country": {"type": "string", "default": "US"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "brave_summarizer_search",
        "description": "Get an AI-summarized answer from Brave Search",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "count": {"type": "integer", "default": 5},
                "summary": {"type": "boolean", "default": True},
            },
            "required": ["query"],
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("BRAVE_SEARCH_API_KEY", "")
    return {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": key,
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("BRAVE_SEARCH_API_KEY", "")
    if not key:
        return {"error": "BRAVE_SEARCH_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(base_url=BRAVE_SEARCH_BASE, headers=_headers(), timeout=30.0) as c:
            if tool_name == "brave_web_search":
                params: dict[str, Any] = {
                    "q": arguments["query"],
                    "count": arguments.get("count", 10),
                    "offset": arguments.get("offset", 0),
                    "safesearch": arguments.get("safesearch", "moderate"),
                }
                for key_name, api_key in [
                    ("country", "country"),
                    ("search_lang", "search_lang"),
                    ("freshness", "freshness"),
                    ("result_filter", "result_filter"),
                ]:
                    if v := arguments.get(key_name):
                        params[api_key] = v
                r = await c.get("/web/search", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "web": data.get("web", {}).get("results", []),
                    "news": data.get("news", {}).get("results", []),
                    "videos": data.get("videos", {}).get("results", []),
                    "query": data.get("query", {}),
                }

            elif tool_name == "brave_news_search":
                params = {
                    "q": arguments["query"],
                    "count": arguments.get("count", 10),
                    "search_lang": arguments.get("search_lang", "en"),
                    "result_filter": "news",
                }
                if country := arguments.get("country"):
                    params["country"] = country
                if freshness := arguments.get("freshness"):
                    params["freshness"] = freshness
                r = await c.get("/web/search", params=params)
                r.raise_for_status()
                data = r.json()
                return {"news": data.get("news", {}).get("results", [])}

            elif tool_name == "brave_local_search":
                params = {
                    "q": arguments["query"],
                    "count": arguments.get("count", 5),
                    "country": arguments.get("country", "US"),
                    "result_filter": "locations",
                }
                r = await c.get("/web/search", params=params)
                r.raise_for_status()
                data = r.json()
                return {"locations": data.get("locations", {}).get("results", [])}

            elif tool_name == "brave_summarizer_search":
                params = {
                    "q": arguments["query"],
                    "count": arguments.get("count", 5),
                    "summary": "1" if arguments.get("summary", True) else "0",
                }
                r = await c.get("/web/search", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "summarizer_key": data.get("summarizer", {}).get("key"),
                    "results": data.get("web", {}).get("results", []),
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("brave_search_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
