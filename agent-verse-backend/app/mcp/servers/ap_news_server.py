"""AP News MCP server — breaking news articles and media content search.

Environment:
  AP_NEWS_API_KEY: AP News API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.ap.org/media/v/content"

TOOL_DEFINITIONS = [
    {
        "name": "ap_news_search_articles",
        "description": "Search AP News articles by keyword, topic, or date range",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
                "from_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "to_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
                "page": {"type": "integer", "description": "Page number"},
                "page_size": {"type": "integer", "description": "Results per page"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ap_news_get_article",
        "description": "Get the full content of a specific AP News article by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "article_id": {"type": "string", "description": "AP News article ID"},
            },
            "required": ["article_id"],
        },
    },
    {
        "name": "ap_news_list_topics",
        "description": "List available news topics and categories in the AP content API",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "ap_news_get_top_headlines",
        "description": "Get the current top headlines from AP News",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "Number of headlines to return"},
                "language": {"type": "string", "description": "Language code (default: en)"},
            },
        },
    },
    {
        "name": "ap_news_search_by_topic",
        "description": "Search AP News articles filtered by a specific topic category",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic name (e.g. politics, sports, technology)"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "page_size": {"type": "integer", "description": "Results per page"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "ap_news_get_breaking_news",
        "description": "Get the latest breaking news items from AP News in real-time",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of breaking news items"},
                "urgency": {"type": "integer", "description": "Minimum urgency level (1-5, where 1 is most urgent)"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("AP_NEWS_API_KEY", "")
    if not api_key:
        return {"error": "AP_NEWS_API_KEY not configured"}

    headers = {"x-api-key": api_key, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "ap_news_search_articles":
                params: dict[str, Any] = {"q": arguments["query"]}
                if "from_date" in arguments:
                    params["dateStart"] = arguments["from_date"]
                if "to_date" in arguments:
                    params["dateEnd"] = arguments["to_date"]
                if "page" in arguments:
                    params["page"] = arguments["page"]
                if "page_size" in arguments:
                    params["count"] = arguments["page_size"]
                r = await client.get(f"{BASE_URL}/search", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "ap_news_get_article":
                r = await client.get(
                    f"{BASE_URL}/{arguments['article_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "ap_news_list_topics":
                r = await client.get(f"{BASE_URL}/categories", headers=headers)
                r.raise_for_status()
                return r.json()

            if tool_name == "ap_news_get_top_headlines":
                params = {"count": arguments.get("page_size", 10)}
                if "language" in arguments:
                    params["language"] = arguments["language"]
                r = await client.get(f"{BASE_URL}/feed", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "ap_news_search_by_topic":
                params = {
                    "category": arguments["topic"],
                    "count": arguments.get("page_size", 10),
                }
                if "from_date" in arguments:
                    params["dateStart"] = arguments["from_date"]
                r = await client.get(f"{BASE_URL}/feed", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "ap_news_get_breaking_news":
                params = {
                    "count": arguments.get("limit", 5),
                    "urgency": arguments.get("urgency", 1),
                }
                r = await client.get(f"{BASE_URL}/feed", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
