"""Tavily MCP server — AI-optimized search and URL content extraction.

Environment:
  TAVILY_API_KEY: Tavily API key (tvly-...)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TAVILY_BASE = "https://api.tavily.com"

TOOL_DEFINITIONS = [
    {
        "name": "tavily_search",
        "description": "Search the web using Tavily AI-powered search, optimized for LLM consumption",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "search_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "default": "basic",
                    "description": "basic is faster, advanced provides more comprehensive results",
                },
                "topic": {
                    "type": "string",
                    "enum": ["general", "news", "finance"],
                    "default": "general",
                },
                "max_results": {"type": "integer", "default": 5, "description": "1–20"},
                "include_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict search to these domains",
                },
                "exclude_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exclude these domains from results",
                },
                "include_answer": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include a short AI-generated answer",
                },
                "include_raw_content": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include raw HTML content of pages",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days back to search (news topic only)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "tavily_extract",
        "description": "Extract clean content from one or more URLs using Tavily",
        "parameters": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of URLs to extract content from",
                },
                "extract_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "default": "basic",
                },
            },
            "required": ["urls"],
        },
    },
    {
        "name": "tavily_qna_search",
        "description": "Ask a question and get a direct AI answer grounded in web search",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Question to answer"},
                "search_depth": {"type": "string", "enum": ["basic", "advanced"], "default": "advanced"},
                "topic": {"type": "string", "enum": ["general", "news"], "default": "general"},
            },
            "required": ["query"],
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("TAVILY_API_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("TAVILY_API_KEY", "")
    if not key:
        return {"error": "TAVILY_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(base_url=TAVILY_BASE, headers=_headers(), timeout=30.0) as c:
            if tool_name == "tavily_search":
                payload: dict[str, Any] = {
                    "query": arguments["query"],
                    "search_depth": arguments.get("search_depth", "basic"),
                    "topic": arguments.get("topic", "general"),
                    "max_results": arguments.get("max_results", 5),
                    "include_answer": arguments.get("include_answer", True),
                    "include_raw_content": arguments.get("include_raw_content", False),
                }
                if incl := arguments.get("include_domains"):
                    payload["include_domains"] = incl
                if excl := arguments.get("exclude_domains"):
                    payload["exclude_domains"] = excl
                if days := arguments.get("days"):
                    payload["days"] = days
                r = await c.post("/search", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "tavily_extract":
                payload = {
                    "urls": arguments["urls"],
                    "extract_depth": arguments.get("extract_depth", "basic"),
                }
                r = await c.post("/extract", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "tavily_qna_search":
                payload = {
                    "query": arguments["query"],
                    "search_depth": arguments.get("search_depth", "advanced"),
                    "topic": arguments.get("topic", "general"),
                    "include_answer": True,
                    "max_results": 5,
                }
                r = await c.post("/search", json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "answer": data.get("answer", ""),
                    "sources": [
                        {"url": result.get("url"), "title": result.get("title")}
                        for result in data.get("results", [])
                    ],
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("tavily_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
